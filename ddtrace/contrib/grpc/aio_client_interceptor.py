import asyncio
import functools
from typing import Callable
from typing import Tuple
from typing import Union

import grpc
from grpc import aio
from grpc.aio._typing import RequestIterableType
from grpc.aio._typing import RequestType
from grpc.aio._typing import ResponseIterableType
from grpc.aio._typing import ResponseType

from .. import trace_utils
from ... import Pin
from ... import Span
from ... import config
from ...constants import ANALYTICS_SAMPLE_RATE_KEY
from ...constants import ERROR_MSG
from ...constants import ERROR_TYPE
from ...constants import SPAN_MEASURED_KEY
from ...ext import SpanTypes
from ...internal.compat import to_unicode
from ...propagation.http import HTTPPropagator
from ..grpc import constants
from ..grpc import utils


def create_aio_client_interceptors(pin, host, port):
    # type: (Pin, str, int) -> Tuple[aio.ClientInterceptor, ...]
    return (
        _UnaryUnaryClientInterceptor(pin, host, port),
        _UnaryStreamClientInterceptor(pin, host, port),
        _StreamUnaryClientInterceptor(pin, host, port),
        _StreamStreamClientInterceptor(pin, host, port),
    )


def _done_callback(span, code, details):
    # type: (Span, grpc.StatusCode, str) -> Callable[[aio.Call], None]
    def func(call):
        # type: (aio.Call) -> None
        try:
            span.set_tag_str(constants.GRPC_STATUS_CODE_KEY, to_unicode(code))

            # Handle server-side error in unary response RPCs
            if code != grpc.StatusCode.OK:
                _handle_error(span, call, code, details)
        finally:
            span.finish()

    return func


def _handle_error(span, call, code, details):
    # type: (Span, aio.Call, grpc.StatusCode, str) -> None
    span.error = 1
    span.set_tag_str(ERROR_MSG, details)
    span.set_tag_str(ERROR_TYPE, to_unicode(code))


def _handle_rpc_error(span, rpc_error):
    # type: (Span, aio.AioRpcError) -> None
    code = to_unicode(rpc_error.code())
    span.error = 1
    span.set_tag_str(constants.GRPC_STATUS_CODE_KEY, code)
    span.set_tag_str(ERROR_MSG, rpc_error.details())
    span.set_tag_str(ERROR_TYPE, code)
    span.finish()


async def _handle_cancelled_error(call, span):
    # type: (aio.Call, Span) -> None
    code = to_unicode(await call.code())
    span.error = 1
    span.set_tag_str(constants.GRPC_STATUS_CODE_KEY, code)
    span.set_tag_str(ERROR_MSG, await call.details())
    span.set_tag_str(ERROR_TYPE, code)
    span.finish()


class _ClientInterceptor:
    def __init__(self, pin, host, port):
        # type: (Pin, str, int) -> None
        self._pin = pin
        self._host = host
        self._port = port

    def _intercept_client_call(self, method_kind, client_call_details):
        # type: (str, aio.ClientCallDetails) -> Tuple[Span, aio.ClientCallDetails]
        tracer = self._pin.tracer

        method_as_str = client_call_details.method.decode()
        span = tracer.trace(
            "grpc",
            span_type=SpanTypes.GRPC,
            service=trace_utils.ext_service(self._pin, config.grpc_aio_client),
            resource=method_as_str,
        )
        span.set_tag(SPAN_MEASURED_KEY)

        utils.set_grpc_method_meta(span, method_as_str, method_kind)
        utils.set_grpc_client_meta(span, self._host, self._port)
        span.set_tag_str(constants.GRPC_SPAN_KIND_KEY, constants.GRPC_SPAN_KIND_VALUE_CLIENT)

        sample_rate = config.grpc_aio_client.get_analytics_sample_rate()
        if sample_rate is not None:
            span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, sample_rate)

        # inject tags from pin
        if self._pin.tags:
            span.set_tags(self._pin.tags)

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
        call,  # type: Union[aio.StreamStreamCall, aio.UnaryStreamCall]
        span,  # type: Span
    ):
        # type: (...) -> ResponseIterableType
        try:
            async for response in call:
                yield response
            code = await call.code()
            details = await call.details()
            # NOTE: The callback is registered after the iteration is done,
            # otherwise `call.code()` and `call.details()` block indefinitely.
            call.add_done_callback(_done_callback(span, code, details))
        except aio.AioRpcError as rpc_error:
            # NOTE: We can also handle the error in done callbacks,
            # but reuse this error handling function used in unary response RPCs.
            _handle_rpc_error(span, rpc_error)
            raise
        except asyncio.CancelledError:
            # NOTE: We can't handle the cancelled error in done callbacks
            # because they cannot handle awaitable functions.
            await _handle_cancelled_error(call, span)
            raise

    # NOTE: `continuation` must be called inside of this function to catch exceptions.
    async def _wrap_unary_response(
        self,
        continuation,  # type: Callable[[], Union[aio.StreamUnaryCall, aio.UnaryUnaryCall]]
        span,  # type: Span
    ):
        # type: (...) -> Union[aio.StreamUnaryCall, aio.UnaryUnaryCall]
        try:
            call = await continuation()
            code = await call.code()
            details = await call.details()
            # NOTE: As both `code` and `details` are available after the RPC is done (= we get `call` object),
            # and we can't call awaitable functions inside the non-async callback,
            # there is no other way but to register the callback here.
            call.add_done_callback(_done_callback(span, code, details))
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
        continuation,  # type: Callable[[aio.ClientCallDetails, RequestType], aio.UnaryUnaryCall]
        client_call_details,  # type: aio.ClientCallDetails
        request,  # type: RequestType
    ):
        # type: (...) -> Union[aio.UnaryUnaryCall, ResponseType]
        span, client_call_details = self._intercept_client_call(
            constants.GRPC_METHOD_KIND_UNARY,
            client_call_details,
        )
        continuation_with_args = functools.partial(continuation, client_call_details, request)
        return await self._wrap_unary_response(continuation_with_args, span)


class _UnaryStreamClientInterceptor(aio.UnaryStreamClientInterceptor, _ClientInterceptor):
    async def intercept_unary_stream(
        self,
        continuation,  # type: Callable[[aio.ClientCallDetails, RequestType], aio.UnaryStreamCall]
        client_call_details,  # type: aio.ClientCallDetails
        request,  # type: RequestType
    ):
        # type: (...) -> Union[aio.UnaryStreamCall, ResponseIterableType]
        span, client_call_details = self._intercept_client_call(
            constants.GRPC_METHOD_KIND_SERVER_STREAMING,
            client_call_details,
        )
        call = await continuation(client_call_details, request)
        return self._wrap_stream_response(call, span)


class _StreamUnaryClientInterceptor(aio.StreamUnaryClientInterceptor, _ClientInterceptor):
    async def intercept_stream_unary(
        self,
        continuation,  # type: Callable[[aio.ClientCallDetails, RequestType], aio.StreamUnaryCall]
        client_call_details,  # type: aio.ClientCallDetails
        request_iterator,  # type: RequestIterableType
    ):
        # type: (...) -> aio.StreamUnaryCall
        span, client_call_details = self._intercept_client_call(
            constants.GRPC_METHOD_KIND_CLIENT_STREAMING,
            client_call_details,
        )
        continuation_with_args = functools.partial(continuation, client_call_details, request_iterator)
        return await self._wrap_unary_response(continuation_with_args, span)


class _StreamStreamClientInterceptor(aio.StreamStreamClientInterceptor, _ClientInterceptor):
    async def intercept_stream_stream(
        self,
        continuation,  # type: Callable[[aio.ClientCallDetails, RequestType], aio.StreamStreamCall]
        client_call_details,  # type: aio.ClientCallDetails
        request_iterator,  # type: RequestIterableType
    ):
        # type: (...) -> Union[aio.StreamStreamCall, ResponseIterableType]
        span, client_call_details = self._intercept_client_call(
            constants.GRPC_METHOD_KIND_BIDI_STREAMING,
            client_call_details,
        )
        call = await continuation(client_call_details, request_iterator)
        return self._wrap_stream_response(call, span)
