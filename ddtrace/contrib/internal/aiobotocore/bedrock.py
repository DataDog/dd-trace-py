import asyncio
import inspect
import json
import sys
from typing import Any
from typing import AsyncIterator
from typing import Awaitable
from typing import Callable
from typing import Optional

import wrapt

from ddtrace import config
from ddtrace._trace.utils_botocore.span_tags import _derive_peer_hostname
from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib.internal.botocore.services.bedrock import _extract_streamed_response
from ddtrace.contrib.internal.botocore.services.bedrock import _extract_streamed_response_metadata
from ddtrace.contrib.internal.botocore.services.bedrock import _extract_text_and_response_reason
from ddtrace.contrib.internal.botocore.services.bedrock import _resolve_application_inference_profile
from ddtrace.contrib.internal.botocore.services.bedrock import _set_llmobs_usage
from ddtrace.contrib.internal.botocore.services.bedrock import handle_bedrock_request
from ddtrace.contrib.internal.botocore.services.bedrock import parse_model_id
from ddtrace.contrib.internal.botocore.services.bedrock import safe_token_count
from ddtrace.contrib.internal.trace_utils import set_service_and_source
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.ext import aws
from ddtrace.ext import http
from ddtrace.internal import core
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.serverless import in_aws_lambda
from ddtrace.internal.span_bus import span_from_context
from ddtrace.llmobs._integrations.base_stream_handler import AsyncStreamHandler
from ddtrace.llmobs._integrations.base_stream_handler import TracedAsyncStream


class AiobotocoreStreamingBodyStreamHandler(AsyncStreamHandler):
    async def process_chunk(self, chunk: dict[str, Any], iterator: Optional[Any] = None) -> None:
        execution_ctx = self.options.get("execution_ctx", {})
        if not execution_ctx["bedrock_integration"].llmobs_enabled:
            return
        self.chunks.append(json.loads(chunk["chunk"]["bytes"]))

    def handle_exception(self, exception: BaseException) -> None:
        core.dispatch(
            "botocore.patched_bedrock_api_call.exception", (self.options.get("execution_ctx", {}), sys.exc_info())
        )

    def finalize_stream(self, exception: Optional[BaseException] = None) -> None:
        if exception:
            return
        execution_ctx = self.options.get("execution_ctx", {})
        if not execution_ctx["bedrock_integration"].llmobs_enabled:
            core.dispatch("botocore.bedrock.process_response", (execution_ctx, {}))
            return
        try:
            _extract_streamed_response_metadata(execution_ctx, self.chunks)
            formatted_response = _extract_streamed_response(execution_ctx, self.chunks)
        except (KeyError, IndexError, TypeError, ValueError):
            if not self.options.get("allow_partial", False):
                raise
            formatted_response = {}
        core.dispatch("botocore.bedrock.process_response", (execution_ctx, formatted_response))


class AiobotocoreConverseStreamHandler(AsyncStreamHandler):
    async def process_chunk(self, chunk: dict[str, Any], iterator: Optional[Any] = None) -> None:
        stream_processor = self.options.get("stream_processor")
        if stream_processor:
            stream_processor.send(chunk)

    def handle_exception(self, exception: BaseException) -> None:
        execution_ctx = self.options.get("execution_ctx", {})
        core.dispatch("botocore.patched_bedrock_api_call.exception", (execution_ctx, sys.exc_info()))

    def finalize_stream(self, exception: Optional[BaseException] = None) -> None:
        if exception:
            return
        stream_processor = self.options.get("stream_processor")
        execution_ctx = self.options.get("execution_ctx", {})
        core.dispatch("botocore.bedrock.process_response_converse", (execution_ctx, stream_processor))


class AiobotocoreTracedAsyncStream(TracedAsyncStream):
    """Traced async stream that owns cancellation and close finalization."""

    def __init__(self, stream: Any, handler: AsyncStreamHandler) -> None:
        super().__init__(stream, handler)
        self._self_finalized = False

    def _finalize(self, exception: Optional[BaseException] = None, allow_partial: bool = False) -> None:
        if self._self_finalized:
            return
        self._self_finalized = True
        if allow_partial:
            self._self_handler.options["allow_partial"] = True
        if exception is not None:
            self._self_handler.handle_exception(exception)
        self._self_handler.finalize_stream(exception)

    async def __aiter__(self) -> AsyncIterator[Any]:
        self._ensure_started()
        exception: Optional[BaseException] = None
        try:
            async for chunk in self._self_async_stream_iter:
                await self._self_handler.process_chunk(chunk, self._self_async_stream_iter)
                # Both Bedrock event-stream handlers are transparent: every SDK event must be
                # returned to the caller after instrumentation has observed it.
                yield chunk
        except asyncio.CancelledError as exc:
            exception = exc
            raise
        except Exception as exc:
            exception = exc
            raise
        finally:
            self._finalize(exception)

    async def __anext__(self) -> Any:
        self._ensure_started()
        try:
            chunk = await self._self_async_stream_iter.__anext__()
            await self._self_handler.process_chunk(chunk, self._self_async_stream_iter)
        except StopAsyncIteration:
            self._finalize()
            raise
        except asyncio.CancelledError as exc:
            self._finalize(exc)
            raise
        except Exception as exc:
            self._finalize(exc)
            raise
        return chunk

    def close(self) -> Any:
        try:
            result = self.__wrapped__.close()
        except asyncio.CancelledError as exc:
            self._finalize(exc)
            raise
        except Exception as exc:
            self._finalize(exc)
            raise

        if inspect.isawaitable(result):

            async def await_close() -> Any:
                try:
                    value = await result
                except asyncio.CancelledError as exc:
                    self._finalize(exc)
                    raise
                except Exception as exc:
                    self._finalize(exc)
                    raise
                else:
                    self._finalize(allow_partial=True)
                    return value

            return await_close()

        self._finalize(allow_partial=True)
        return result

    async def aclose(self) -> None:
        aclose = getattr(self.__wrapped__, "aclose", None)
        try:
            if callable(aclose):
                result = aclose()
            else:
                result = self.__wrapped__.close()
            if inspect.isawaitable(result):
                await result
        except asyncio.CancelledError as exc:
            self._finalize(exc)
            raise
        except Exception as exc:
            self._finalize(exc)
            raise
        else:
            self._finalize(allow_partial=True)


def make_aiobotocore_streaming_body_traced_event_stream(stream: Any, execution_ctx: core.ExecutionContext) -> Any:
    return AiobotocoreTracedAsyncStream(
        stream,
        AiobotocoreStreamingBodyStreamHandler(None, None, None, None, execution_ctx=execution_ctx),
    )


def make_aiobotocore_converse_traced_stream(stream: Any, execution_ctx: core.ExecutionContext) -> Any:
    stream_processor = execution_ctx["bedrock_integration"]._converse_output_stream_processor()
    next(stream_processor)
    return AiobotocoreTracedAsyncStream(
        stream,
        AiobotocoreConverseStreamHandler(
            None, None, None, None, execution_ctx=execution_ctx, stream_processor=stream_processor
        ),
    )


class AiobotocoreStreamingBody(wrapt.ObjectProxy):
    """Collect an async Bedrock response body and finalize its tracing span exactly once."""

    def __init__(self, body: Any, execution_ctx: core.ExecutionContext) -> None:
        super().__init__(body)
        self._self_execution_ctx = execution_ctx
        self._self_chunks: list[bytes] = []
        self._self_finished = False
        self._self_collect_response = execution_ctx["bedrock_integration"].llmobs_enabled

    def _append_chunk(self, chunk: bytes) -> None:
        if self._self_collect_response:
            self._self_chunks.append(chunk)

    def _extend_chunks(self, chunks: list[bytes]) -> None:
        if self._self_collect_response:
            self._self_chunks.extend(chunks)

    def _dispatch_exception(self, exc_info: Optional[Any] = None) -> None:
        if self._self_finished:
            return
        self._self_finished = True
        core.dispatch(
            "botocore.patched_bedrock_api_call.exception",
            (self._self_execution_ctx, exc_info or sys.exc_info()),
        )

    def _finish(self, allow_partial: bool = False) -> None:
        if self._self_finished:
            return
        self._self_finished = True
        if not self._self_collect_response:
            core.dispatch("botocore.bedrock.process_response", (self._self_execution_ctx, {}))
            return
        try:
            raw_body = b"".join(self._self_chunks)
            if raw_body:
                try:
                    response = json.loads(raw_body)
                except (UnicodeDecodeError, ValueError):
                    if not allow_partial:
                        raise
                    response = {}
            else:
                response = {}
            formatted_response = _extract_text_and_response_reason(self._self_execution_ctx, response)
            core.dispatch("botocore.bedrock.process_response", (self._self_execution_ctx, formatted_response))
        except Exception:
            # `_self_finished` is already set so dispatch directly instead of using
            # `_dispatch_exception`, whose guard intentionally prevents double finalization.
            core.dispatch(
                "botocore.patched_bedrock_api_call.exception", (self._self_execution_ctx, sys.exc_info())
            )
            if not allow_partial:
                raise

    def _fully_consumed(self) -> bool:
        body = self.__wrapped__
        # Current aiobotocore inherits botocore's `_amount_read` / `_content_length`.
        # Older wrappers have also exposed `_self_*` variants, so accept both shapes.
        for amount_name, length_name in (
            ("_amount_read", "_content_length"),
            ("_self_amount_read", "_self_content_length"),
        ):
            amount_read = getattr(body, amount_name, None)
            content_length = getattr(body, length_name, None)
            if amount_read is not None and content_length is not None:
                try:
                    return int(amount_read) >= int(content_length)
                except (TypeError, ValueError):
                    pass

        tell = getattr(body, "tell", None)
        content_length = getattr(body, "_content_length", None)
        if callable(tell) and content_length is not None:
            try:
                return int(tell()) >= int(content_length)
            except (TypeError, ValueError):
                return False
        return False

    async def read(self, amt: Optional[int] = None) -> bytes:
        try:
            body = await self.__wrapped__.read() if amt is None else await self.__wrapped__.read(amt)
            self._append_chunk(body)
            if amt is None or amt < 0 or (amt > 0 and not body) or self._fully_consumed():
                self._finish()
            return body
        except asyncio.CancelledError:
            self._dispatch_exception()
            raise
        except Exception:
            self._dispatch_exception()
            raise

    async def readinto(self, buffer: bytearray) -> int:
        try:
            amount_read = await self.__wrapped__.readinto(buffer)
            self._append_chunk(bytes(buffer[:amount_read]))
            if (len(buffer) > 0 and amount_read == 0) or self._fully_consumed():
                self._finish()
            return amount_read
        except asyncio.CancelledError:
            self._dispatch_exception()
            raise
        except Exception:
            self._dispatch_exception()
            raise

    async def readlines(self) -> list[bytes]:
        try:
            lines = await self.__wrapped__.readlines()
            self._extend_chunks(lines)
            self._finish()
            return lines
        except asyncio.CancelledError:
            self._dispatch_exception()
            raise
        except Exception:
            self._dispatch_exception()
            raise

    async def __aiter__(self) -> AsyncIterator[bytes]:
        completed = False
        try:
            async for body in self.__wrapped__:
                self._append_chunk(body)
                yield body
            completed = True
        except asyncio.CancelledError:
            self._dispatch_exception()
            raise
        except Exception:
            self._dispatch_exception()
            raise
        finally:
            if not self._self_finished:
                self._finish(allow_partial=not completed)

    async def __anext__(self) -> bytes:
        try:
            body = await self.__wrapped__.__anext__()
            self._append_chunk(body)
            if self._fully_consumed():
                self._finish()
            return body
        except StopAsyncIteration:
            self._finish()
            raise
        except asyncio.CancelledError:
            self._dispatch_exception()
            raise
        except Exception:
            self._dispatch_exception()
            raise

    anext = __anext__

    async def iter_chunks(self, chunk_size: int = 1024) -> AsyncIterator[bytes]:
        while True:
            chunk = await self.read(chunk_size)
            if chunk == b"":
                break
            yield chunk

    async def iter_lines(self, chunk_size: int = 1024, keepends: bool = False) -> AsyncIterator[bytes]:
        pending = b""
        async for chunk in self.iter_chunks(chunk_size):
            lines = (pending + chunk).splitlines(True)
            for line in lines[:-1]:
                yield line.splitlines(keepends)[0]
            pending = lines[-1]
        if pending:
            yield pending.splitlines(keepends)[0]

    def close(self) -> Any:
        try:
            result = self.__wrapped__.close()
        except asyncio.CancelledError:
            self._dispatch_exception()
            raise
        except Exception:
            self._dispatch_exception()
            raise

        if inspect.isawaitable(result):

            async def await_close() -> Any:
                try:
                    value = await result
                except asyncio.CancelledError:
                    self._dispatch_exception()
                    raise
                except Exception:
                    self._dispatch_exception()
                    raise
                else:
                    self._finish(allow_partial=True)
                    return value

            return await_close()

        self._finish(allow_partial=True)
        return result

    async def aclose(self) -> None:
        aclose = getattr(self.__wrapped__, "aclose", None)
        try:
            if callable(aclose):
                result = aclose()
            else:
                result = self.__wrapped__.close()
            if inspect.isawaitable(result):
                await result
        except asyncio.CancelledError:
            self._dispatch_exception()
            raise
        except Exception:
            self._dispatch_exception()
            raise
        else:
            self._finish(allow_partial=True)

    async def __aenter__(self) -> "AiobotocoreStreamingBody":
        await self.__wrapped__.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc_value, traceback) -> Any:
        try:
            return await self.__wrapped__.__aexit__(exc_type, exc_value, traceback)
        except asyncio.CancelledError:
            self._dispatch_exception()
            raise
        except Exception:
            self._dispatch_exception()
            raise
        finally:
            if not self._self_finished:
                if exc_type is not None and exc_value is not None:
                    self._dispatch_exception((exc_type, exc_value, traceback))
                else:
                    self._finish(allow_partial=True)


def make_aiobotocore_streaming_body_traced_stream(
    streaming_body: Any, execution_ctx: core.ExecutionContext
) -> AiobotocoreStreamingBody:
    return AiobotocoreStreamingBody(streaming_body, execution_ctx)


def _record_bedrock_response_metadata(ctx: core.ExecutionContext, result: dict[str, Any]) -> None:
    metadata = result["ResponseMetadata"]
    http_headers = metadata["HTTPHeaders"]

    total_tokens = None
    input_tokens = http_headers.get("x-amzn-bedrock-input-token-count", "")
    output_tokens = http_headers.get("x-amzn-bedrock-output-token-count", "")
    cache_read_tokens = None
    cache_write_tokens = None

    if ctx["resource"] == "Converse":
        usage = result.get("usage", {})
        if usage:
            input_tokens = usage.get("inputTokens", input_tokens)
            output_tokens = usage.get("outputTokens", output_tokens)
            total_tokens = usage.get("totalTokens", total_tokens)
            cache_read_tokens = usage.get("cacheReadInputTokenCount", None) or usage.get("cacheReadInputTokens", None)
            cache_write_tokens = usage.get("cacheWriteInputTokenCount", None) or usage.get(
                "cacheWriteInputTokens", None
            )
        if "stopReason" in result:
            ctx.set_item("llmobs.stop_reason", result.get("stopReason"))

    _set_llmobs_usage(
        ctx,
        safe_token_count(input_tokens),
        safe_token_count(output_tokens),
        safe_token_count(total_tokens),
        safe_token_count(cache_read_tokens),
        safe_token_count(cache_write_tokens),
    )


def _set_apm_request_tags(ctx: core.ExecutionContext, function_vars: dict[str, Any]) -> None:
    span = span_from_context(ctx)
    if span is None:
        return

    instance = function_vars["instance"]
    endpoint_name = function_vars["endpoint_name"]
    operation = function_vars["operation"]
    params = function_vars.get("params")
    service = function_vars["service"]

    set_service_and_source(span, service, config.aiobotocore)
    span._set_attribute(COMPONENT, config.aiobotocore.integration_name)
    span._set_attribute(SPAN_KIND, SpanKind.CLIENT)
    span._set_attribute(_SPAN_MEASURED_KEY, 1)
    span.resource = "{}.{}".format(endpoint_name, operation.lower())

    if params and not config.aiobotocore["tag_no_params"]:
        aws._add_api_param_span_tags(span, endpoint_name, params)

    region_name = getattr(getattr(instance, "meta", None), "region_name", None)
    if in_aws_lambda():
        hostname = _derive_peer_hostname(endpoint_name, region_name, params)
        if hostname:
            span._set_attribute("peer.service", hostname)

    meta = {
        "aws.agent": "aiobotocore",
        "aws.operation": operation,
        "aws.region": region_name,
        "region": region_name,
    }
    if region_name:
        meta["aws.partition"] = aws.get_aws_partition(region_name)
    span.set_tags(meta)


def _set_apm_response_tags(ctx: core.ExecutionContext, result: dict[str, Any]) -> None:
    span = span_from_context(ctx)
    if span is None:
        return
    response_meta = result.get("ResponseMetadata", {})
    status_code = response_meta.get("HTTPStatusCode")
    if status_code is not None:
        span._set_attribute(http.STATUS_CODE, status_code)
        if 500 <= status_code < 600:
            span.error = 1
    if "RetryAttempts" in response_meta:
        span.set_tag("retry_attempts", response_meta["RetryAttempts"])
    request_id = response_meta.get("RequestId")
    if request_id:
        span._set_attribute("aws.requestid", request_id)
    response_headers = response_meta.get("HTTPHeaders", {})
    request_id2 = response_headers.get("x-amz-id-2")
    if request_id2:
        span._set_attribute("aws.requestid2", request_id2)


def handle_aiobotocore_bedrock_response(
    ctx: core.ExecutionContext,
    result: dict[str, Any],
) -> dict[str, Any]:
    _record_bedrock_response_metadata(ctx, result)
    _set_apm_response_tags(ctx, result)

    if ctx["resource"] == "Converse":
        core.dispatch("botocore.bedrock.process_response_converse", (ctx, result))
        return result
    if ctx["resource"] == "ConverseStream":
        if "stream" in result:
            result["stream"] = make_aiobotocore_converse_traced_stream(result["stream"], ctx)
        return result
    if ctx["resource"] == "InvokeModelWithResponseStream":
        result["body"] = make_aiobotocore_streaming_body_traced_event_stream(result["body"], ctx)
        return result

    result["body"] = make_aiobotocore_streaming_body_traced_stream(result["body"], ctx)
    return result


def _handle_aiobotocore_bedrock_request(ctx: core.ExecutionContext) -> None:
    """Extract Bedrock request metadata without consuming file-like InvokeModel bodies."""
    if ctx["resource"] in ("Converse", "ConverseStream"):
        handle_bedrock_request(ctx)
        return

    body = ctx["params"].get("body")
    if isinstance(body, (str, bytes, bytearray)):
        handle_bedrock_request(ctx)
        return

    # Botocore accepts seekable/file-like blobs for InvokeModel. Reading them here would both
    # block the event loop and risk advancing the SDK payload before it is sent. Keep tracing
    # active but omit prompt-specific request metadata for payloads we cannot safely inspect.
    request_params: dict[str, Any] = {}
    core.dispatch("botocore.patched_bedrock_api_call.started", (ctx, request_params))
    if ctx["bedrock_integration"].llmobs_enabled:
        ctx.set_item("llmobs.request_params", request_params)


async def patched_aiobotocore_bedrock_api_call(
    original_func: Callable[..., Awaitable[Any]],
    instance: Any,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    function_vars: dict[str, Any],
) -> Any:
    params = function_vars.get("params") or {}
    integration = function_vars["integration"]
    model_id = params.get("modelId", "")
    model_provider, model_name = parse_model_id(model_id)
    # Cache hits are safe to share with the synchronous integration. Do not perform
    # the synchronous GetInferenceProfile control-plane lookup on the event loop.
    model_id, model_provider, model_name = _resolve_application_inference_profile(
        model_id, model_provider, model_name, llmobs_enabled=integration.llmobs_enabled
    )
    submit_to_llmobs = integration.llmobs_enabled and "embed" not in model_name

    context_vars = {
        **function_vars,
        "span_type": SpanTypes.LLM if submit_to_llmobs else SpanTypes.HTTP,
        "resource": function_vars["operation"],
        "bedrock_integration": integration,
        "model_provider": model_provider,
        "model_name": model_name,
        "model_id": model_id,
    }
    with core.context_with_data(
        "botocore.patched_bedrock_api_call",
        pin=function_vars["pin"],
        span_name=function_vars["trace_operation"],
        service=function_vars["service"],
        resource=context_vars["resource"],
        span_type=context_vars["span_type"],
        call_trace=True,
        bedrock_integration=integration,
        params=params,
        model_provider=model_provider,
        model_name=model_name,
        model_id=model_id,
        instance=instance,
    ) as ctx:
        function_vars = {**function_vars, "instance": instance}
        _set_apm_request_tags(ctx, function_vars)
        try:
            _handle_aiobotocore_bedrock_request(ctx)
            result = await original_func(*args, **kwargs)
            return handle_aiobotocore_bedrock_response(ctx, result)
        except asyncio.CancelledError:
            core.dispatch("botocore.patched_bedrock_api_call.exception", (ctx, sys.exc_info()))
            raise
        except Exception:
            core.dispatch("botocore.patched_bedrock_api_call.exception", (ctx, sys.exc_info()))
            raise
