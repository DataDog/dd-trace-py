import asyncio
from collections import deque
from collections.abc import AsyncGenerator
from contextvars import ContextVar
from typing import TYPE_CHECKING
from typing import Any
from typing import Optional
from typing import Union
from urllib.parse import urlparse

from litellm._logging import verbose_proxy_logger
from litellm.integrations.custom_guardrail import CustomGuardrail
from litellm.proxy._types import UserAPIKeyAuth
from litellm.proxy.common_utils.callback_utils import add_guardrail_to_applied_guardrails_header
from litellm.types.guardrails import GuardrailEventHooks
from litellm.types.llms.openai import AllMessageValues
from litellm.types.llms.openai import ChatCompletionUserMessage
from litellm.types.utils import CallTypes
from litellm.types.utils import CallTypesLiteral
from litellm.types.utils import Choices
from litellm.types.utils import LLMResponseTypes
from litellm.types.utils import ModelResponse
from litellm.types.utils import TextCompletionResponse


if TYPE_CHECKING:
    from litellm.caching.caching import DualCache

from ddtrace.aiguard import AIGuardAbortError
from ddtrace.aiguard import AIGuardClientError
from ddtrace.aiguard import ContentPart
from ddtrace.aiguard import Function
from ddtrace.aiguard import ImageURL
from ddtrace.aiguard import MCPServer
from ddtrace.aiguard import Message
from ddtrace.aiguard import Options
from ddtrace.aiguard import ToolCall
from ddtrace.aiguard import new_ai_guard_client
from ddtrace.aiguard._common import _get
from ddtrace.aiguard._constants import AI_GUARD
from ddtrace.aiguard._context import _Claim
from ddtrace.aiguard._context import is_aiguard_context_active
from ddtrace.aiguard._context import reset_aiguard_context_active
from ddtrace.aiguard._context import set_aiguard_context_active
from ddtrace.aiguard.integrations._anthropic import _safe_convert_messages
from ddtrace.aiguard.integrations._anthropic import _safe_convert_response
from ddtrace.aiguard.integrations._openai_chat import _convert_openai_response
from ddtrace.aiguard.integrations._openai_chat_streaming import reconstruct_openai_chat
from ddtrace.aiguard.integrations._openai_responses import _convert_openai_response_input
from ddtrace.aiguard.integrations._openai_responses import _convert_openai_response_output
from ddtrace.aiguard.integrations._openai_responses import _hosted_mcp_servers
from ddtrace.aiguard.integrations._openai_responses import _mcp_servers_payload
from ddtrace.internal.settings.aiguard import aiguard_config
from ddtrace.internal.utils.openai import openai_construct_completion_from_streamed_chunks


GUARDRAIL_NAME = "datadog_ai_guard"

# The post-call hooks evaluate the chat and text completion answers of every route and response mode.
_CHAT_CALL_TYPES = frozenset({"completion", "acompletion", "text_completion", "atext_completion"})
# They evaluate these answers only on the create routes, and only when they are not streamed.
_ROUTE_CALL_TYPES = frozenset({"responses", "aresponses", "anthropic_messages"})

# This variable keeps the claim of the proxy request. The proxy runs the pre-call hook in the
# request task, and the model call and the stream get a copy of that context.
_REQUEST_CLAIM: ContextVar[Optional[_Claim]] = ContextVar("ai_guard_litellm_request_claim", default=None)
# This variable is True only while the streaming hook reads a chunk of the model stream. Some litellm versions
# run the post-call success hook on the full stream, in a task that they start during that read.
_READING_STREAM: ContextVar[bool] = ContextVar("ai_guard_litellm_reading_stream", default=False)


def _claim_request() -> None:
    """Make the provider listeners skip the model call of this proxy request."""
    if not is_aiguard_context_active():
        _REQUEST_CLAIM.set(set_aiguard_context_active())


def _release_request() -> None:
    reset_aiguard_context_active(_REQUEST_CLAIM.get())


def _call_type_from_route(data: dict[str, Any]) -> Optional[CallTypesLiteral]:
    """Get the call type from the proxy route of the request."""
    path = urlparse(str((data.get("proxy_server_request") or {}).get("url") or "")).path.rstrip("/")
    if path.endswith("/v1/messages"):
        return "anthropic_messages"
    # Only the create route ends with /responses. Retrieve, cancel, input_items and compact add a suffix.
    if path.endswith("/responses"):
        return "aresponses"
    if path.endswith("/completions") and not path.endswith("/chat/completions"):
        return "text_completion"
    return None


def _text_blocks(content: Any) -> Any:
    """Copy an Anthropic content list with each string item as a text block."""
    if not isinstance(content, list):
        return content
    return [{"type": "text", "text": block} if isinstance(block, str) else block for block in content]


def _message_text_blocks(message: Any) -> Any:
    """Copy an Anthropic message with the string items of its content and tool_result lists as text blocks."""
    if not isinstance(message, dict) or not isinstance(message.get("content"), list):
        return message
    content = []
    for block in _text_blocks(message["content"]):
        if isinstance(block, dict) and block.get("type") == "tool_result" and isinstance(block.get("content"), list):
            block = {**block, "content": _text_blocks(block["content"])}
        content.append(block)
    return {**message, "content": content}


def _answer_is_evaluated(data: dict[str, Any], call_type: Optional[str]) -> bool:
    """Return True if the post-call hooks evaluate the answer of this proxy request."""
    if call_type in _CHAT_CALL_TYPES:
        return True
    if call_type not in _ROUTE_CALL_TYPES or data.get("stream"):
        return False
    return _call_type_from_route(data) in ("aresponses", "anthropic_messages")


def _responses_text_parts(input_: Any) -> Any:
    """Copy a Responses input with each chat-style text part as an input_text part."""
    if not isinstance(input_, list):
        return input_
    items = []
    for item in input_:
        if isinstance(item, dict):
            for key in ("content", "output"):
                if isinstance(item.get(key), list):
                    parts = [
                        {**part, "type": "input_text"} if isinstance(part, dict) and part.get("type") == "text" else part
                        for part in item[key]
                    ]
                    item = {**item, key: parts}
        items.append(item)
    return items


async def _read_next(stream: Any) -> Any:
    """Read the next chunk of the model stream, with the read flag set during this read only."""
    token = _READING_STREAM.set(True)
    try:
        return await stream.__anext__()
    finally:
        _READING_STREAM.reset(token)


class DatadogAIGuardGuardrailException(Exception):
    def __init__(self, status_code: int, cause: AIGuardAbortError):
        super().__init__(f"Datadog AI Guard: request blocked '{cause.reason}'")
        # litellm sends the message attribute to the client. Without it, litellm adds a traceback to a streamed error.
        self.message = str(self)
        self.status_code = status_code
        self.action = cause.action
        self.reason = cause.reason
        self.tags = cause.tags
        self.sds = cause.sds
        self.__cause__ = cause


class DatadogAIGuardGuardrail(CustomGuardrail):  # type: ignore[misc]
    def __init__(
        self,
        block: Optional[bool] = None,
        **kwargs: Any,
    ):
        """
        Initialize the DatadogAIGuardGuardrail class.

        Args:
            block: whether to enable blocking or not when an evaluation is not safe.
                   If None, delegates to the DD_AI_GUARD_BLOCK env var / default config.
        """
        self._block = block
        self._client = new_ai_guard_client()

        kwargs.setdefault("guardrail_name", GUARDRAIL_NAME)
        super().__init__(**kwargs)

    @staticmethod
    def _convert_request_messages(messages: list[AllMessageValues]) -> list[Message]:
        result: list[Message] = []
        # FIFO queue: synthetic ids assigned to legacy function_call tool-calls,
        # consumed in order by the matching function-role response messages.
        pending_fc_ids: deque[str] = deque()
        fc_counter = 0

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content")

            api_msg = Message(role=role)
            if isinstance(content, list):
                content_parts: list[ContentPart] = []
                for part in content:
                    if isinstance(part, str):
                        content_parts.append(ContentPart(type="text", text=part))
                    elif isinstance(part, dict):
                        part_type = part.get("type", "")
                        if part_type == "text":
                            content_parts.append(ContentPart(type="text", text=part.get("text", "")))
                        elif part_type == "image_url":
                            image_url = part.get("image_url") or {}
                            url = image_url.get("url", "") if isinstance(image_url, dict) else str(image_url)
                            content_parts.append(ContentPart(type="image_url", image_url=ImageURL(url=url)))
                if content_parts:
                    api_msg["content"] = content_parts
            elif content is not None:
                api_msg["content"] = content if isinstance(content, str) else str(content)

            if role == "tool":
                tool_call_id = msg.get("tool_call_id")
                if tool_call_id:
                    api_msg["tool_call_id"] = str(tool_call_id)
            elif role == "function":
                # Legacy function response: correlate with the earliest pending
                # function_call by popping the synthetic id from the queue.
                api_msg["role"] = "tool"
                api_msg["tool_call_id"] = pending_fc_ids.popleft() if pending_fc_ids else ""
            elif role == "assistant":
                api_tool_calls = []
                tool_calls = msg.get("tool_calls")
                if tool_calls and isinstance(tool_calls, list):
                    for tc in tool_calls:
                        if isinstance(tc, dict):
                            function_info = tc.get("function", {})
                            api_tool_calls.append(
                                ToolCall(
                                    id=tc.get("id") or "",
                                    function=Function(
                                        name=function_info.get("name") or "",
                                        arguments=function_info.get("arguments") or "{}",
                                    ),
                                )
                            )
                function_call = msg.get("function_call")
                if function_call and isinstance(function_call, dict):
                    synthetic_id = f"fc_{fc_counter}"
                    fc_counter += 1
                    pending_fc_ids.append(synthetic_id)
                    api_tool_calls.append(
                        ToolCall(
                            id=synthetic_id,
                            function=Function(
                                name=function_call.get("name") or "",
                                arguments=function_call.get("arguments") or "{}",
                            ),
                        )
                    )
                if api_tool_calls:
                    api_msg["tool_calls"] = api_tool_calls

            result.append(api_msg)

        return result

    @staticmethod
    def _convert_response_messages(choices: list[Choices]) -> list[Message]:
        result: list[Message] = []

        for choice in choices:
            message = choice.message
            api_msg = Message(role="assistant")
            if message.content is not None:
                api_msg["content"] = message.content

            tool_calls = []
            if message.tool_calls:
                for tc in message.tool_calls:
                    tool_calls.append(
                        ToolCall(
                            id=tc.id,
                            function=Function(
                                name=tc.function.name or "",
                                arguments=tc.function.arguments or "{}",
                            ),
                        )
                    )
            if message.function_call:
                synthetic_id = f"fc_{id(message.function_call):x}"
                tool_calls.append(
                    ToolCall(
                        id=synthetic_id,
                        function=Function(
                            name=message.function_call.name or "",
                            arguments=message.function_call.arguments or "{}",
                        ),
                    )
                )

            if tool_calls:
                api_msg["tool_calls"] = tool_calls

            result.append(api_msg)

        return result

    @staticmethod
    def _convert_text_prompt(prompt: Any) -> list[AllMessageValues]:
        # Like litellm text_completion, this makes one user message for each prompt string.
        # Token ID lists have no text, so they give no message.
        if isinstance(prompt, str):
            return [ChatCompletionUserMessage(role="user", content=prompt)]
        if isinstance(prompt, list):
            return [ChatCompletionUserMessage(role="user", content=p) for p in prompt if isinstance(p, str)]
        return []

    @staticmethod
    def _convert_stream_messages(chunks: list[Any]) -> list[Message]:
        if not chunks or not isinstance(chunks[0], TextCompletionResponse):
            return _convert_openai_response(reconstruct_openai_chat(chunks))
        choices_by_index: dict[int, list[Any]] = {}
        for chunk in chunks:
            for choice in getattr(chunk, "choices", None) or []:
                choices_by_index.setdefault(getattr(choice, "index", 0) or 0, []).append(choice)
        return [
            Message(role="assistant", content=openai_construct_completion_from_streamed_chunks(choices)["text"])
            for choices in choices_by_index.values()
        ]

    def _resolve_block(self, dynamic_params: dict[str, Any]) -> bool:
        # Blocking is a trusted server-side policy (litellm config / DD_AI_GUARD_BLOCK).
        # Request-body dynamic params may only strengthen it (monitor -> block), never weaken
        # it, so a proxy caller cannot disable enforcement on its own request.
        server_block = aiguard_config._ai_guard_block if self._block is None else bool(self._block)
        raw = dynamic_params.get("block")
        if raw is None:
            return server_block
        # intentionally fail-secure — only the literal "false" is treated as
        # "don't add block"; any other value errs toward blocking. The `or server_block`
        # below means dynamic params can never weaken server policy regardless.
        dynamic_block = raw if isinstance(raw, bool) else str(raw).lower() != "false"
        return server_block or dynamic_block

    async def _run_ai_guard_check(
        self,
        messages: list[Message],
        dynamic_params: dict[str, Any],
        can_block: bool = True,
        mcp_servers: Optional[list[MCPServer]] = None,
    ) -> bool:
        """Return True when AI Guard completed the evaluation, also for a DENY in monitor mode.

        With can_block False, the evaluation reports the verdict and never blocks.
        """
        try:
            verbose_proxy_logger.debug("Datadog AI Guard: Making request to endpoint")
            block = can_block and self._resolve_block(dynamic_params)
            response = await asyncio.to_thread(
                self._client.evaluate,
                messages,
                Options(block=block),
                source=AI_GUARD.SOURCE_AUTO,
                integration=AI_GUARD.INTEGRATION_LITELLM,
                mcp_servers=mcp_servers,
            )
            if response["action"] in ("DENY", "ABORT"):
                verbose_proxy_logger.debug(
                    "Datadog AI Guard: monitor mode - violation detected but allowing request. "
                    "action=%s, reason=%s, tags=%s",
                    response["action"],
                    response["reason"],
                    response["tags"],
                )
            return True
        except AIGuardAbortError as e:
            raise DatadogAIGuardGuardrailException(403, e)
        except AIGuardClientError:
            verbose_proxy_logger.error("Datadog AI Guard: Error calling AI Guard service", exc_info=True)
            return False

    async def _on_request(
        self, data: dict[str, Any], call_type: CallTypesLiteral, claim: bool = False
    ) -> Optional[Union[Exception, str, dict[str, Any]]]:
        dynamic_params = self.get_guardrail_dynamic_request_body_params(request_data=data)

        if call_type is None:
            # Some litellm versions send no call type to the during_call hook of the text completion
            # and messages routes.
            call_type = _call_type_from_route(data)
        try:
            # The proxy also sends call types that are not CallTypes values, for example "embeddings".
            request_call_type = CallTypes(call_type)
        except ValueError:
            verbose_proxy_logger.debug("Datadog AI Guard: not running guardrail. Unknown call type %s", call_type)
            return data
        if request_call_type == CallTypes.anthropic_messages:
            # litellm returns the Anthropic messages unconverted here, and the system prompt is a separate field.
            messages, exact = self._anthropic_prompt(data)
            # After the chat conversion, do not claim, so that the provider listeners also check the model call.
            claim = claim and exact
        else:
            messages = None
            if request_call_type in (CallTypes.responses, CallTypes.aresponses):
                # litellm drops the variables of a saved prompt, but the provider gets them.
                messages = self._responses_prompt(data)
            if not messages:
                request_messages = self.get_guardrails_messages_for_call_type(call_type=request_call_type, data=data)
                if not request_messages and call_type in ("text_completion", "atext_completion"):
                    request_messages = self._convert_text_prompt(data.get("prompt"))
                messages = self._convert_request_messages(request_messages) if request_messages else None
        if not messages:
            verbose_proxy_logger.debug("Datadog AI Guard: not running guardrail. No messages found in request")
            return data

        completed = await self._run_ai_guard_check(messages, dynamic_params)
        # After an AI Guard error, do not claim: the provider listeners then evaluate the prompt again.
        if claim and completed:
            _claim_request()

        # Some litellm versions record the applied guardrail only in a metadata dict that is not empty.
        if not data.get("metadata"):
            data["metadata"] = {"applied_guardrails": []}
        add_guardrail_to_applied_guardrails_header(request_data=data, guardrail_name=self.guardrail_name)
        return data

    async def _on_response(self, data: dict[str, Any], response: LLMResponseTypes) -> Any:
        response_messages: list[Message] = []
        mcp_servers: Optional[dict[str, MCPServer]] = None
        route = _call_type_from_route(data)

        if isinstance(response, ModelResponse) and response.choices:
            converted_messages = self._convert_response_messages(list(response.choices))
            response_messages.extend(converted_messages)
        elif isinstance(response, TextCompletionResponse) and response.choices:
            response_messages.extend(Message(role="assistant", content=c.text or "") for c in response.choices)
        elif route == "aresponses":
            try:
                tools = data.get("tools")
                if not isinstance(tools, (list, tuple)):
                    # A saved prompt resolves its tools server-side; the response echoes them.
                    tools = _get(response, "tools")
                mcp_servers = _hosted_mcp_servers(tools)
            except Exception:
                # As in the OpenAI listener, a tools declaration that cannot be read only drops the MCP metadata.
                verbose_proxy_logger.debug("Datadog AI Guard: cannot read the hosted MCP servers", exc_info=True)
            try:
                response_messages = _convert_openai_response_output(response, mcp_servers)
            except Exception:
                verbose_proxy_logger.debug("Datadog AI Guard: cannot convert the Responses answer", exc_info=True)
        elif route == "anthropic_messages":
            response_messages = _safe_convert_response(response) or []

        return await self._evaluate_response(data, response_messages, mcp_servers=mcp_servers)

    async def _on_stream_response(self, data: dict[str, Any], chunks: list[Any], can_block: bool = True) -> None:
        try:
            response_messages = self._convert_stream_messages(chunks)
        except Exception:
            # A rebuild error must not stop the stream, so this fails open.
            verbose_proxy_logger.debug("Datadog AI Guard: cannot rebuild the streamed response", exc_info=True)
            return
        await self._evaluate_response(data, response_messages, can_block)

    async def _evaluate_response(
        self,
        data: dict[str, Any],
        response_messages: list[Message],
        can_block: bool = True,
        mcp_servers: Optional[dict[str, MCPServer]] = None,
    ) -> None:
        if not response_messages:
            verbose_proxy_logger.debug("Datadog AI Guard: not running guardrail. No messages found in response")
            return None

        dynamic_params = self.get_guardrail_dynamic_request_body_params(request_data=data)
        messages = self._prompt_messages(data, mcp_servers)
        if messages is None:
            verbose_proxy_logger.debug("Datadog AI Guard: not running guardrail. Cannot convert the request")
            return None
        messages.extend(response_messages)
        mcp_payload = _mcp_servers_payload(messages, mcp_servers)
        if mcp_payload:
            await self._run_ai_guard_check(messages, dynamic_params, can_block, mcp_servers=mcp_payload)
        else:
            await self._run_ai_guard_check(messages, dynamic_params, can_block)

        add_guardrail_to_applied_guardrails_header(request_data=data, guardrail_name=self.guardrail_name)

        return None

    def _prompt_messages(
        self, data: dict[str, Any], mcp_servers: Optional[dict[str, MCPServer]]
    ) -> Optional[list[Message]]:
        """Convert the prompt from the request body for the route of the request, or None if the conversion fails.

        The client controls the request metadata, so the prompt never comes from there.
        """
        route = _call_type_from_route(data)
        if route == "anthropic_messages":
            return self._anthropic_prompt(data)[0]
        if route == "aresponses":
            return self._responses_prompt(data, mcp_servers)
        return self._convert_request_messages(data.get("messages") or self._convert_text_prompt(data.get("prompt")))

    def _responses_prompt(
        self, data: dict[str, Any], mcp_servers: Optional[dict[str, MCPServer]] = None
    ) -> Optional[list[Message]]:
        """Convert the instructions, input and prompt variables of a Responses request, or None if it fails."""
        try:
            # litellm also accepts chat-style text parts and sends them to the model.
            input_ = _responses_text_parts(data.get("input"))
            return _convert_openai_response_input(
                data.get("instructions"), input_, prompt=data.get("prompt"), mcp_servers=mcp_servers
            )
        except Exception:
            verbose_proxy_logger.debug("Datadog AI Guard: cannot convert the Responses request", exc_info=True)
            return None

    def _anthropic_prompt(self, data: dict[str, Any]) -> tuple[Optional[list[Message]], bool]:
        """Return the AI Guard messages of a Messages request, and True if the Anthropic converter read the body.

        Otherwise return the chat conversion of the messages and False, or (None, False) if it also fails.
        """
        messages = data.get("messages")
        if isinstance(messages, list):
            # The Anthropic converter skips string items, but litellm can send them to the model as text.
            messages = [_message_text_blocks(message) for message in messages]
        converted = _safe_convert_messages(_text_blocks(data.get("system")), messages)
        if converted is not None:
            return converted, True
        # The Anthropic converter cannot read this body. The chat conversion still checks the text of its messages,
        # but it misses the system prompt and the tool, image, document and thinking blocks.
        try:
            return self._convert_request_messages(data.get("messages") or []), False
        except Exception:
            verbose_proxy_logger.debug("Datadog AI Guard: cannot convert the Messages request", exc_info=True)
            return None, False

    async def async_pre_call_hook(
        self, user_api_key_dict: UserAPIKeyAuth, cache: "DualCache", data: dict[str, Any], call_type: CallTypesLiteral
    ) -> Optional[Union[Exception, str, dict[str, Any]]]:
        verbose_proxy_logger.debug("Datadog AI Guard: Pre-Call Hook for call_type: %s", call_type)
        event_type: GuardrailEventHooks = GuardrailEventHooks.pre_call
        if not self.should_run_guardrail(data=data, event_type=event_type):
            return data
        # Claim only proxy requests whose answer the post-call hooks also evaluate.
        # Other callers run this hook in their own task, where a claim stays.
        claim = (
            "proxy_server_request" in data
            and _answer_is_evaluated(data, call_type)
            and self.should_run_guardrail(data=data, event_type=GuardrailEventHooks.post_call) is True
        )
        return await self._on_request(data, call_type, claim=claim)

    async def async_moderation_hook(
        self, data: dict[str, Any], user_api_key_dict: UserAPIKeyAuth, call_type: CallTypesLiteral
    ) -> Any:
        verbose_proxy_logger.debug("Datadog AI Guard: Moderation Hook for call_type: %s", call_type)
        event_type: GuardrailEventHooks = GuardrailEventHooks.during_call
        if not self.should_run_guardrail(data=data, event_type=event_type):
            return data
        return await self._on_request(data, call_type)

    async def async_post_call_success_hook(
        self, data: dict[str, Any], user_api_key_dict: UserAPIKeyAuth, response: LLMResponseTypes
    ) -> Any:
        verbose_proxy_logger.debug("Datadog AI Guard: Post-Call Success Hook")
        try:
            event_type: GuardrailEventHooks = GuardrailEventHooks.post_call
            if not self.should_run_guardrail(data=data, event_type=event_type):
                # The proxy uses a return value that is not None as the new response.
                return None
            if _READING_STREAM.get():
                # The streaming hook reads the model stream now, and it evaluates this answer itself.
                return None
            return await self._on_response(data, response)
        finally:
            _release_request()

    async def async_post_call_streaming_iterator_hook(
        self, user_api_key_dict: UserAPIKeyAuth, response: Any, request_data: dict[str, Any]
    ) -> AsyncGenerator[Any, None]:
        """With stream analysis on, hold a streamed chat or text answer until AI Guard evaluates it.

        When it is off, evaluate the answer after the last chunk. Other streams pass through live.
        """
        verbose_proxy_logger.debug("Datadog AI Guard: Post-Call Streaming Iterator Hook")
        if not self.should_run_guardrail(data=request_data, event_type=GuardrailEventHooks.post_call):
            # The post_call mode does not apply to this request, so the stream passes through unchanged.
            async for chunk in response:
                yield chunk
            return
        # This first step runs in the request context. Keep the claim of this request, because the asyncio
        # finalizer can close this generator in the context of another request.
        claim = _REQUEST_CLAIM.get()
        try:
            stream = response.__aiter__()
            try:
                first = await _read_next(stream)
            except StopAsyncIteration:
                return
            # Chat and text completion chunks have a list of choices. Responses API events and SSE bytes do not.
            if not isinstance(getattr(first, "choices", None), list):
                yield first
                async for chunk in stream:
                    yield chunk
                return
            chunks = [first]
            if aiguard_config._ai_guard_analyze_stream_responses_enabled:
                while True:
                    try:
                        chunks.append(await _read_next(stream))
                    except StopAsyncIteration:
                        break
                # The model stream has ended. Release the claim now, also when the client never reads the replay.
                reset_aiguard_context_active(claim)
                await self._on_stream_response(request_data, chunks)
                for chunk in chunks:
                    yield chunk
                return
            yield first
            while True:
                try:
                    chunk = await _read_next(stream)
                except StopAsyncIteration:
                    break
                chunks.append(chunk)
                yield chunk
            reset_aiguard_context_active(claim)
            try:
                # The client already has all chunks, so this evaluation reports the verdict and cannot block.
                await self._on_stream_response(request_data, chunks, can_block=False)
            except Exception:
                # An error here must not turn a delivered stream into an error event.
                verbose_proxy_logger.debug("Datadog AI Guard: cannot evaluate the streamed response", exc_info=True)
        finally:
            reset_aiguard_context_active(claim)

    async def async_post_call_failure_hook(
        self,
        request_data: dict[str, Any],
        original_exception: Exception,
        user_api_key_dict: UserAPIKeyAuth,
        traceback_str: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        # The proxy calls this hook for each failed request, also when the post_call mode does not apply.
        _release_request()
