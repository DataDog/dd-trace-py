import asyncio
import functools
from typing import Callable  # noqa:F401
from typing import Union  # noqa:F401

import grpc
from grpc import aio
from grpc.aio._typing import RequestIterableType
from grpc.aio._typing import RequestType
from grpc.aio._typing import ResponseIterableType
from grpc.aio._typing import ResponseType

from ddtrace import config
from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.constants import ERROR_MSG
from ddtrace.constants import ERROR_TYPE
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib import trace_utils
from ddtrace.contrib.internal.grpc import constants
from ddtrace.contrib.internal.grpc import utils
from ddtrace.contrib.internal.trace_utils import set_service_and_source
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.logger import get_logger
from ddtrace.internal.schema import schematize_url_operation
from ddtrace.internal.schema.span_attribute_schema import SpanDirection
from ddtrace.propagation.http import HTTPPropagator
from ddtrace.trace import Span
from ddtrace.trace import tracer


log = get_logger(__name__)


def create_aio_client_interceptors(host: str, port: int) -> tuple[aio.ClientInterceptor, ...]:
    return (
        _UnaryUnaryClientInterceptor(host, port),
        _UnaryStreamClientInterceptor(host, port),
        _StreamUnaryClientInterceptor(host, port),
        _StreamStreamClientInterceptor(host, port),
    )


def _handle_add_callback(call, callback):
    try:
        call.add_done_callback(callback)
    except NotImplementedError:
        # add_done_callback is not implemented in UnaryUnaryCallResponse
        # https://github.com/grpc/grpc/blob/c54c69dcdd483eba78ed8dbc98c60a8c2d069758/src/python/grpcio/grpc/aio/_interceptor.py#L1058
        # If callback is not called, we need to finish the span here
        callback(call)


def _done_callback_unary(span: Span, code: grpc.StatusCode, details: str) -> Callable[[aio.Call], None]:
    def func(call: aio.Call) -> None:
        try:
            span._set_attribute(constants.GRPC_STATUS_CODE_KEY, str(code))

            # Handle server-side error in unary response RPCs
            if code != grpc.StatusCode.OK:
                _handle_error(span, code, details)
        finally:
            span.finish()

    return func


_GRPC_AIO_ERROR_HANDLED = "_dd.grpc_aio.error_handled"


def _claim_stream_error(span: Span) -> bool:
    if span._get_ctx_item(_GRPC_AIO_ERROR_HANDLED):
        return False
    span._set_ctx_item(_GRPC_AIO_ERROR_HANDLED, True)
    return True


async def _finish_stream_terminal_state(call: aio.Call, span: Span) -> None:
    if not _claim_stream_error(span):
        return
    try:
        code = await call.code()
        details = await call.details()
        if isinstance(details, bytes):
            details = details.decode("utf-8", errors="ignore")
        else:
            details = str(details)

        if code == grpc.StatusCode.OK:
            span._set_attribute(constants.GRPC_STATUS_CODE_KEY, str(code))
        else:
            _set_error_attrs(span, str(code), details)
    finally:
        span.finish()


def _done_callback_stream(span: Span) -> Callable[[aio.Call], None]:
    def func(call: aio.Call) -> None:
        # AIDEV-NOTE: gRPC can invoke this done callback while the stream iterator is still
        # surfacing its terminal exception. Successful calls can be finalized synchronously,
        # but non-OK calls need the authoritative async code/details accessors. The shared
        # `_claim_stream_error` gate makes callback-side and iterator-side finalization mutually exclusive.
        if span._get_ctx_item(_GRPC_AIO_ERROR_HANDLED):
            return
        if not call.done():
            log.warning("Grpc call has not completed, unable to set status code and details on span.")
            span.finish()
            return
        try:
            # Fast-path successful calls without scheduling another task. For non-OK calls,
            # use the async accessors below because repr details can still be transport placeholders.
            code, _details = utils._parse_rpc_repr_string(call.__repr__(), grpc)
        except ValueError:
            code = None
        if code == grpc.StatusCode.OK:
            span._set_attribute(constants.GRPC_STATUS_CODE_KEY, str(code))
            span.finish()
            return

        # A stream may never be consumed, so there may be no iterator-side error handler to own
        # finalization. Schedule an async finalizer that can read authoritative code/details.
        # `_claim_stream_error` makes this race-safe with `_wrap_stream_response`: whichever path
        # starts handling the terminal state first owns tagging and finishing the span.
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            log.warning("Unable to schedule async grpc terminal-state handling; finishing span without status tags.")
            span.finish()
            return
        loop.create_task(_finish_stream_terminal_state(call, span))

    return func


def _handle_error(span: Span, code: grpc.StatusCode, details: str) -> None:
    span.error = 1
    span._set_attribute(ERROR_MSG, details)
    span._set_attribute(ERROR_TYPE, str(code))


def _set_error_attrs(span: Span, code_str: str, details: str) -> None:
    span.error = 1
    span._set_attribute(constants.GRPC_STATUS_CODE_KEY, code_str)
    span._set_attribute(ERROR_MSG, details)
    span._set_attribute(ERROR_TYPE, code_str)


def _handle_rpc_error(span: Span, rpc_error: aio.AioRpcError) -> None:
    code = str(rpc_error.code())
    span.error = 1
    span._set_attribute(constants.GRPC_STATUS_CODE_KEY, code)
    details = rpc_error.details()
    if isinstance(details, bytes):
        details = details.decode("utf-8", errors="ignore")
    else:
        details = str(details)
    span._set_attribute(ERROR_MSG, details)
    span._set_attribute(ERROR_TYPE, code)
    span.finish()


async def _handle_cancelled_error(call: aio.Call, span: Span) -> None:
    if not _claim_stream_error(span):
        return
    try:
        _set_error_attrs(span, str(await call.code()), await call.details())
    finally:
        span.finish()


async def _handle_stream_rpc_error(span: Span, call: aio.Call, rpc_error: aio.AioRpcError) -> None:
    # AIDEV-NOTE: `rpc_error.details()` may still contain a transport placeholder while gRPC is
    # completing trailers. Await the call's code/details for the authoritative terminal state,
    # and claim ownership before awaiting so the done callback cannot flush the span concurrently.
    if not _claim_stream_error(span):
        return
    try:
        try:
            code = await call.code()
            details = await call.details()
        except Exception:
            code = rpc_error.code()
            details = rpc_error.details()
        if isinstance(details, bytes):
            details = details.decode("utf-8", errors="ignore")
        _set_error_attrs(span, str(code), details)
    finally:
        span.finish()


class _ClientInterceptor:
    def __init__(self, host: str, port: int) -> None:
        self._host = host
        self._port = port

    def _intercept_client_call(
        self, method_kind: str, client_call_details: aio.ClientCallDetails
    ) -> tuple[Span, aio.ClientCallDetails]:
        method_as_str = client_call_details.method.decode()
        span = tracer.trace(
            schematize_url_operation("grpc", protocol="grpc", direction=SpanDirection.OUTBOUND),
            span_type=SpanTypes.GRPC,
            resource=method_as_str,
        )
        set_service_and_source(span, trace_utils.ext_service(None, config.grpc_aio_client), config.grpc_aio_client)

        span._set_attribute(COMPONENT, config.grpc_aio_client.integration_name)

        # set span.kind to the type of operation being performed
        span._set_attribute(SPAN_KIND, SpanKind.CLIENT)

        span._set_attribute(_SPAN_MEASURED_KEY, 1)

        utils.set_grpc_method_meta(span, method_as_str, method_kind)
        utils.set_grpc_client_meta(span, self._host, self._port)
        span._set_attribute(constants.GRPC_SPAN_KIND_KEY, constants.GRPC_SPAN_KIND_VALUE_CLIENT)

        # propagate distributed tracing headers if available
        headers = {}
        if config.grpc_aio_client.distributed_tracing_enabled:
            HTTPPropagator.inject(span.context, headers)

        metadata = []
        if client_call_details.metadata is not None:
            metadata = list(client_call_details.metadata)
        metadata.extend(headers.items())

        client_call_details = aio.ClientCallDetails(
            client_call_details.method,
            client_call_details.timeout,
            metadata,
            client_call_details.credentials,
            client_call_details.wait_for_ready,
        )

        return span, client_call_details

    # NOTE: Since this function is executed as an async generator when the RPC is called,
    # `continuation` must be called before the RPC.
    async def _wrap_stream_response(
        self,
        call: Union[aio.StreamStreamCall, aio.UnaryStreamCall],
        span: Span,
    ) -> ResponseIterableType:
        try:
            async for response in call:
                yield response
        except StopAsyncIteration:
            # Callback will handle span finishing
            _handle_cancelled_error()
            raise
        except aio.AioRpcError as rpc_error:
            # NOTE: The callback and iterator can observe termination concurrently.
            # `_handle_stream_rpc_error` claims ownership synchronously before awaiting,
            # so exactly one path writes terminal tags and finishes the span.
            await _handle_stream_rpc_error(span, call, rpc_error)
            raise
        except asyncio.CancelledError:
            await _handle_cancelled_error(call, span)
            raise

    # NOTE: `continuation` must be called inside of this function to catch exceptions.
    async def _wrap_unary_response(
        self,
        continuation: Callable[[], Union[aio.StreamUnaryCall, aio.UnaryUnaryCall]],
        span: Span,
    ) -> Union[aio.StreamUnaryCall, aio.UnaryUnaryCall]:
        call = None
        try:
            call = await continuation()
            code = await call.code()
            details = await call.details()
            # NOTE: As both `code` and `details` are available after the RPC is done (= we get `call` object),
            # and we can't call awaitable functions inside the non-async callback,
            # there is no other way but to register the callback here.
            _handle_add_callback(call, _done_callback_unary(span, code, details))
            return call
        except aio.AioRpcError as rpc_error:
            # NOTE: `AioRpcError` is raised in `await continuation(...)`
            # and `call` object is not assigned yet in that case.
            # So we can't handle the error in done callbacks.
            _handle_rpc_error(span, rpc_error)
            raise
        except asyncio.CancelledError:
            if call is not None:
                await _handle_cancelled_error(call, span)
            else:
                _set_error_attrs(span, str(grpc.StatusCode.CANCELLED), "Locally cancelled by application!")
                span.finish()
            raise


class _UnaryUnaryClientInterceptor(aio.UnaryUnaryClientInterceptor, _ClientInterceptor):
    async def intercept_unary_unary(
        self,
        continuation: Callable[[aio.ClientCallDetails, RequestType], aio.UnaryUnaryCall],
        client_call_details: aio.ClientCallDetails,
        request: RequestType,
    ) -> Union[aio.UnaryUnaryCall, ResponseType]:
        span, client_call_details = self._intercept_client_call(
            constants.GRPC_METHOD_KIND_UNARY,
            client_call_details,
        )
        continuation_with_args = functools.partial(continuation, client_call_details, request)
        return await self._wrap_unary_response(continuation_with_args, span)


class _UnaryStreamClientInterceptor(aio.UnaryStreamClientInterceptor, _ClientInterceptor):
    async def intercept_unary_stream(
        self,
        continuation: Callable[[aio.ClientCallDetails, RequestType], aio.UnaryStreamCall],
        client_call_details: aio.ClientCallDetails,
        request: RequestType,
    ) -> Union[aio.UnaryStreamCall, ResponseIterableType]:
        span, client_call_details = self._intercept_client_call(
            constants.GRPC_METHOD_KIND_SERVER_STREAMING,
            client_call_details,
        )
        call = await continuation(client_call_details, request)
        _handle_add_callback(call, _done_callback_stream(span))
        return self._wrap_stream_response(call, span)


class _StreamUnaryClientInterceptor(aio.StreamUnaryClientInterceptor, _ClientInterceptor):
    async def intercept_stream_unary(
        self,
        continuation: Callable[[aio.ClientCallDetails, RequestType], aio.StreamUnaryCall],
        client_call_details: aio.ClientCallDetails,
        request_iterator: RequestIterableType,
    ) -> aio.StreamUnaryCall:
        span, client_call_details = self._intercept_client_call(
            constants.GRPC_METHOD_KIND_CLIENT_STREAMING,
            client_call_details,
        )
        continuation_with_args = functools.partial(continuation, client_call_details, request_iterator)
        return await self._wrap_unary_response(continuation_with_args, span)


class _StreamStreamClientInterceptor(aio.StreamStreamClientInterceptor, _ClientInterceptor):
    async def intercept_stream_stream(
        self,
        continuation: Callable[[aio.ClientCallDetails, RequestType], aio.StreamStreamCall],
        client_call_details: aio.ClientCallDetails,
        request_iterator: RequestIterableType,
    ) -> Union[aio.StreamStreamCall, ResponseIterableType]:
        span, client_call_details = self._intercept_client_call(
            constants.GRPC_METHOD_KIND_BIDI_STREAMING,
            client_call_details,
        )
        call = await continuation(client_call_details, request_iterator)
        _handle_add_callback(call, _done_callback_stream(span))
        return self._wrap_stream_response(call, span)
