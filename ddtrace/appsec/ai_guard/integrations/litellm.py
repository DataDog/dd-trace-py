import asyncio
from collections import deque
from typing import TYPE_CHECKING
from typing import Any
from typing import Optional
from typing import Union

from litellm._logging import verbose_proxy_logger
from litellm.integrations.custom_guardrail import CustomGuardrail
from litellm.proxy._types import UserAPIKeyAuth
from litellm.proxy.common_utils.callback_utils import add_guardrail_to_applied_guardrails_header
from litellm.types.guardrails import GuardrailEventHooks
from litellm.types.llms.openai import AllMessageValues
from litellm.types.utils import CallTypes
from litellm.types.utils import CallTypesLiteral
from litellm.types.utils import Choices
from litellm.types.utils import LLMResponseTypes
from litellm.types.utils import ModelResponse


if TYPE_CHECKING:
    from litellm.caching.caching import DualCache

from ddtrace.appsec.ai_guard import AIGuardAbortError
from ddtrace.appsec.ai_guard import AIGuardClientError
from ddtrace.appsec.ai_guard import ContentPart
from ddtrace.appsec.ai_guard import Function
from ddtrace.appsec.ai_guard import ImageURL
from ddtrace.appsec.ai_guard import Message
from ddtrace.appsec.ai_guard import Options
from ddtrace.appsec.ai_guard import ToolCall
from ddtrace.appsec.ai_guard import new_ai_guard_client
from ddtrace.internal.settings.asm import ai_guard_config


GUARDRAIL_NAME = "datadog_ai_guard"


class DatadogAIGuardGuardrailException(Exception):
    def __init__(self, status_code: int, cause: AIGuardAbortError):
        super().__init__(f"Datadog AI Guard: request blocked '{cause.reason}'")
        self.status_code = status_code
        self.action = cause.action
        self.reason = cause.reason
        self.tags = cause.tags
        self.sds = cause.sds
        self.__cause__ = cause


class DatadogAIGuardGuardrail(CustomGuardrail):
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
        pending_fc_ids: deque = deque()
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

    def _resolve_block(self, dynamic_params: dict[str, Any]) -> bool:
        raw = dynamic_params.get("block")
        if raw is None:
            if self._block is None:
                return ai_guard_config._ai_guard_block
            return bool(self._block)
        return raw if isinstance(raw, bool) else str(raw).lower() != "false"

    async def _run_ai_guard_check(
        self,
        messages: list[Message],
        dynamic_params: dict[str, Any],
    ) -> None:
        try:
            verbose_proxy_logger.debug("Datadog AI Guard: Making request to endpoint")
            block = self._resolve_block(dynamic_params)
            response = await asyncio.to_thread(self._client.evaluate, messages, Options(block=block))
            if response["action"] in ("DENY", "ABORT"):
                verbose_proxy_logger.debug(
                    "Datadog AI Guard: monitor mode - violation detected but allowing request. "
                    "action=%s, reason=%s, tags=%s",
                    response["action"],
                    response["reason"],
                    response["tags"],
                )
        except AIGuardAbortError as e:
            raise DatadogAIGuardGuardrailException(403, e)
        except AIGuardClientError:
            verbose_proxy_logger.error("Datadog AI Guard: Error calling AI Guard service", exc_info=True)

    async def _on_request(self, data: dict, call_type: CallTypesLiteral) -> Optional[Union[Exception, str, dict]]:
        dynamic_params = self.get_guardrail_dynamic_request_body_params(request_data=data)

        request_messages = self.get_guardrails_messages_for_call_type(
            call_type=CallTypes(call_type),
            data=data,
        )
        if not request_messages:
            verbose_proxy_logger.debug("Datadog AI Guard: not running guardrail. No messages found in request")
            return data

        ai_guard_messages = self._convert_request_messages(request_messages)
        data.setdefault("metadata", {})["ai_guard_messages"] = ai_guard_messages
        await self._run_ai_guard_check(ai_guard_messages, dynamic_params)

        add_guardrail_to_applied_guardrails_header(request_data=data, guardrail_name=self.guardrail_name)
        return data

    async def _on_response(self, data: dict, response: LLMResponseTypes) -> Any:
        dynamic_params = self.get_guardrail_dynamic_request_body_params(request_data=data)

        response_messages: list[Message] = []

        if isinstance(response, ModelResponse) and response.choices:
            converted_messages = self._convert_response_messages(list(response.choices))
            response_messages.extend(converted_messages)

        if not response_messages:
            verbose_proxy_logger.debug("Datadog AI Guard: not running guardrail. No messages found in response")
            return None

        messages: list[Message] = list(data.get("metadata", {}).get("ai_guard_messages", []))
        messages.extend(response_messages)
        await self._run_ai_guard_check(messages, dynamic_params)

        add_guardrail_to_applied_guardrails_header(request_data=data, guardrail_name=self.guardrail_name)

        return None

    async def async_pre_call_hook(
        self, user_api_key_dict: UserAPIKeyAuth, cache: "DualCache", data: dict, call_type: CallTypesLiteral
    ) -> Optional[Union[Exception, str, dict]]:
        verbose_proxy_logger.debug("Datadog AI Guard: Pre-Call Hook for call_type: %s", call_type)
        event_type: GuardrailEventHooks = GuardrailEventHooks.pre_call
        if not self.should_run_guardrail(data=data, event_type=event_type):
            return data
        return await self._on_request(data, call_type)

    async def async_moderation_hook(
        self, data: dict, user_api_key_dict: UserAPIKeyAuth, call_type: CallTypesLiteral
    ) -> Any:
        verbose_proxy_logger.debug("Datadog AI Guard: Moderation Hook for call_type: %s", call_type)
        event_type: GuardrailEventHooks = GuardrailEventHooks.during_call
        if not self.should_run_guardrail(data=data, event_type=event_type):
            return data
        return await self._on_request(data, call_type)

    async def async_post_call_success_hook(
        self, data: dict, user_api_key_dict: UserAPIKeyAuth, response: LLMResponseTypes
    ) -> Any:
        verbose_proxy_logger.debug("Datadog AI Guard: Post-Call Success Hook")
        event_type: GuardrailEventHooks = GuardrailEventHooks.post_call
        if not self.should_run_guardrail(data=data, event_type=event_type):
            return data
        return await self._on_response(data, response)
