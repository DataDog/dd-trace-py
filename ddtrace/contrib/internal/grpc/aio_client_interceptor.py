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


def _done_callback_stream(span: Span) -> Callable[[aio.Call], None]:
    def func(call: aio.Call) -> None:
        # AIDEV-NOTE: gRPC can mark the call done and invoke this callback while
        # the stream iterator's `_raise_for_status()` is still building the
        # `AioRpcError` — i.e. before control reaches our `except aio.AioRpcError`
        # handler and `_handle_stream_rpc_error` has had a chance to set the ctx
        # flag. So the flag check alone is not sufficient: on any non-OK terminal
        # state we must also defer to the awaited handler, because parsing
        # `call.__repr__()` here returns the transport-level placeholder
        # ("Internal error from Core") and finishing flushes the span before the
        # handler can apply the authoritative `await call.code()` / `details()`.
        if span._get_ctx_item(_GRPC_AIO_ERROR_HANDLED):
            return
        if not call.done():
            log.warning("Grpc call has not completed, unable to set status code and details on span.")
            span.finish()
            return
        try:
            # we need to call __repr__ as we cannot call code() or details() since they are both async
            code, _details = utils._parse_rpc_repr_string(call.__repr__(), grpc)
        except ValueError:
            # ValueError is thrown from _parse_rpc_repr_string
            log.warning("Unable to parse async grpc string for status code and details.")
            span.finish()
            return
        if code != grpc.StatusCode.OK:
            # Non-OK terminal state: gRPC will surface an AioRpcError (or
            # CancelledError) to the consumer of `_wrap_stream_response`, so an
            # awaited error handler will run and own finishing the span with
            # authoritative values. Bail before writing repr-derived tags or
            # calling span.finish().
            return
        span._set_attribute(constants.GRPC_STATUS_CODE_KEY, str(code))
        span.finish()

    return func


def _handle_error(span: Span, code: grpc.StatusCode, details: str) -> None:
    span.error = 1
    span._set_attribute(ERROR_MSG, details)
    span._set_attribute(ERROR_TYPE, str(code))


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
    # Mark synchronously before awaiting so `_done_callback_stream` bails out if it
    # fires while we're suspended on `await call.code()` / `await call.details()`.
    # The done callback also defers on any non-OK terminal state, so this handler
    # is the sole owner of `span.finish()` for the cancelled path — wrap the body
    # in try/finally so the span is still finished if an await raises.
    span._set_ctx_item(_GRPC_AIO_ERROR_HANDLED, True)
    try:
        code = str(await call.code())
        details = await call.details()
        span.error = 1
        span._set_attribute(constants.GRPC_STATUS_CODE_KEY, code)
        span._set_attribute(ERROR_MSG, details)
        span._set_attribute(ERROR_TYPE, code)
    finally:
        span.finish()


async def _handle_stream_rpc_error(span: Span, call: aio.Call, rpc_error: aio.AioRpcError) -> None:
    # AIDEV-NOTE: When a server aborts a streaming RPC, gRPC can surface the
    # AioRpcError to the client before trailing metadata has been fully processed.
    # In that window, rpc_error.details() returns the transport-level placeholder
    # "Internal error from Core" instead of the application-set abort details.
    # Awaiting call.code()/call.details() blocks until the call is fully terminated
    # (trailers received), so it returns the authoritative final state. Fall back
    # to the exception's snapshot only if the call cannot resolve its final state.
    # Mark synchronously before awaiting so `_done_callback_stream` bails out if it
    # fires while we're suspended — otherwise it would flush the span with stale
    # tags parsed from call.__repr__() before our authoritative values land.
    span._set_ctx_item(_GRPC_AIO_ERROR_HANDLED, True)
    try:
        try:
            code = await call.code()
            details = await call.details()
        except Exception:
            code = rpc_error.code()
            details = rpc_error.details()
        if isinstance(details, bytes):
            details = details.decode("utf-8", errors="ignore")
        else:
            details = str(details)
        code_str = str(code)
        span.error = 1
        span._set_attribute(constants.GRPC_STATUS_CODE_KEY, code_str)
        span._set_attribute(ERROR_MSG, details)
        span._set_attribute(ERROR_TYPE, code_str)
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
            _handle_add_callback(call, _done_callback_stream(span))
            async for response in call:
                yield response
        except StopAsyncIteration:
            # Callback will handle span finishing
            _handle_cancelled_error()
            raise
        except aio.AioRpcError as rpc_error:
            # NOTE: We can also handle the error in done callbacks, but capturing
            # error tags here means we hold onto the call object and can read its
            # authoritative final state instead of rpc_error's racy snapshot.
            await _handle_stream_rpc_error(span, call, rpc_error)
            raise
        except asyncio.CancelledError:
            # NOTE: We can't handle the cancelled error in done callbacks
            # because they cannot handle awaitable functions.
            await _handle_cancelled_error(call, span)
            raise

    # NOTE: `continuation` must be called inside of this function to catch exceptions.
    async def _wrap_unary_response(
        self,
        continuation: Callable[[], Union[aio.StreamUnaryCall, aio.UnaryUnaryCall]],
        span: Span,
    ) -> Union[aio.StreamUnaryCall, aio.UnaryUnaryCall]:
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
        return self._wrap_stream_response(call, span)
