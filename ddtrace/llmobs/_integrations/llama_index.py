from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._constants import INPUT_DOCUMENTS
from ddtrace.llmobs._constants import INPUT_MESSAGES
from ddtrace.llmobs._constants import INPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import METADATA
from ddtrace.llmobs._constants import METRICS
from ddtrace.llmobs._constants import MODEL_NAME
from ddtrace.llmobs._constants import MODEL_PROVIDER
from ddtrace.llmobs._constants import OUTPUT_MESSAGES
from ddtrace.llmobs._constants import OUTPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import OUTPUT_VALUE
from ddtrace.llmobs._constants import SPAN_KIND
from ddtrace.llmobs._constants import TOTAL_TOKENS_METRIC_KEY
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.llmobs._utils import _get_attr
from ddtrace.llmobs.types import Document
from ddtrace.llmobs.types import Message
from ddtrace.trace import Span


log = get_logger(__name__)


MODEL_TAG = "llama_index.request.model"

SUPPORTED_OPERATIONS = ["chat", "completion", "embedding"]


class LlamaIndexIntegration(BaseLLMIntegration):
    """LLMObs integration for llama_index."""

    _integration_name = "llama_index"

    def _set_base_span_tags(
        self,
        span: Span,
        model: Optional[str] = None,
        **kwargs: Dict[str, Any],
    ) -> None:
        """Set base level tags that should be present on all llama_index spans."""
        if model is not None:
            span._set_tag_str(MODEL_TAG, model)

    def _llmobs_set_tags(
        self,
        span: Span,
        args: List[Any],
        kwargs: Dict[str, Any],
        response: Optional[Any] = None,
        operation: str = "",
    ) -> None:
        """Extract prompt/response tags from a request and set them as temporary "_ml_obs.*" tags."""
        if operation not in SUPPORTED_OPERATIONS:
            log.warning("Unsupported operation: %s", operation)
            return

        if operation == "chat":
            self._llmobs_set_tags_from_chat(span, args, kwargs, response)
        elif operation == "completion":
            self._llmobs_set_tags_from_completion(span, args, kwargs, response)
        elif operation == "embedding":
            self._llmobs_set_tags_from_embedding(span, args, kwargs, response)

    def _llmobs_set_tags_from_chat(
        self,
        span: Span,
        args: List[Any],
        kwargs: Dict[str, Any],
        response: Optional[Any] = None,
    ) -> None:
        """Set tags for chat operations."""
        parameters = {}
        if kwargs.get("temperature"):
            parameters["temperature"] = kwargs.get("temperature")
        if kwargs.get("max_tokens"):
            parameters["max_tokens"] = kwargs.get("max_tokens")

        # Extract input messages - llama-index uses positional arg 'messages' (list of ChatMessage)
        messages = []
        if args:
            messages = args[0] if args else []
        messages = kwargs.get("messages", messages)

        input_messages = self._extract_input_messages_from_chat(messages)

        output_messages: List[Message] = [Message(content="")]
        if not span.error and response is not None:
            output_messages = self._extract_output_messages_from_chat(response)

        metrics = self._extract_usage_from_response(response) if response else {}

        span._set_ctx_items(
            {
                SPAN_KIND: "llm",
                MODEL_NAME: span.get_tag(MODEL_TAG) or "",
                MODEL_PROVIDER: "llama_index",
                INPUT_MESSAGES: input_messages,
                METADATA: parameters if parameters else {},
                OUTPUT_MESSAGES: output_messages,
                METRICS: metrics,
            }
        )

    def _llmobs_set_tags_from_completion(
        self,
        span: Span,
        args: List[Any],
        kwargs: Dict[str, Any],
        response: Optional[Any] = None,
    ) -> None:
        """Set tags for completion operations."""
        parameters = {}
        if kwargs.get("temperature"):
            parameters["temperature"] = kwargs.get("temperature")
        if kwargs.get("max_tokens"):
            parameters["max_tokens"] = kwargs.get("max_tokens")

        # Extract input - llama-index complete takes 'prompt' as first positional arg
        prompt = ""
        if args:
            prompt = args[0] if args else ""
        prompt = kwargs.get("prompt", prompt)

        input_messages = [Message(content=str(prompt), role="user")]

        output_messages: List[Message] = [Message(content="")]
        if not span.error and response is not None:
            output_messages = self._extract_output_messages_from_completion(response)

        metrics = self._extract_usage_from_response(response) if response else {}

        span._set_ctx_items(
            {
                SPAN_KIND: "llm",
                MODEL_NAME: span.get_tag(MODEL_TAG) or "",
                MODEL_PROVIDER: "llama_index",
                INPUT_MESSAGES: input_messages,
                METADATA: parameters if parameters else {},
                OUTPUT_MESSAGES: output_messages,
                METRICS: metrics,
            }
        )

    def _llmobs_set_tags_from_embedding(
        self,
        span: Span,
        args: List[Any],
        kwargs: Dict[str, Any],
        response: Optional[Any] = None,
    ) -> None:
        """Set tags for embedding operations."""
        # Extract input - get_text_embedding takes 'text' as first positional arg
        text = ""
        if args:
            text = args[0] if args else ""
        text = kwargs.get("text", text)

        input_documents: List[Document] = [Document(text=str(text))]

        output_value = ""
        if not span.error and response is not None:
            # Response is a list of floats (the embedding vector)
            if isinstance(response, list):
                embedding_dim = len(response)
                output_value = f"[1 embedding(s) returned with size {embedding_dim}]"

        span._set_ctx_items(
            {
                SPAN_KIND: "embedding",
                MODEL_NAME: span.get_tag(MODEL_TAG) or "",
                MODEL_PROVIDER: "llama_index",
                INPUT_DOCUMENTS: input_documents,
                OUTPUT_VALUE: output_value,
            }
        )

    def _extract_input_messages_from_chat(self, messages: Any) -> List[Message]:
        """Extract input messages from llama-index ChatMessage list."""
        input_messages: List[Message] = []

        if not messages:
            return input_messages

        # Handle if messages is not a list
        if not isinstance(messages, list):
            messages = [messages]

        for message in messages:
            content = ""
            role = "user"

            # llama-index ChatMessage has 'blocks' or 'content' property
            if hasattr(message, "content"):
                content = message.content or ""
            elif hasattr(message, "blocks"):
                # blocks is a list of content blocks
                blocks = message.blocks or []
                content_parts = []
                for block in blocks:
                    if hasattr(block, "text"):
                        content_parts.append(block.text)
                    elif isinstance(block, str):
                        content_parts.append(block)
                content = " ".join(content_parts)

            # Get role
            if hasattr(message, "role"):
                role_val = message.role
                # role could be a MessageRole enum
                if hasattr(role_val, "value"):
                    role = str(role_val.value)
                else:
                    role = str(role_val)

            if isinstance(content, str):
                input_messages.append(Message(content=content, role=role))
            elif isinstance(content, list):
                # Handle multi-part content
                for part in content:
                    if isinstance(part, str):
                        input_messages.append(Message(content=part, role=role))
                    elif hasattr(part, "text"):
                        input_messages.append(Message(content=str(part.text), role=role))

        return input_messages if input_messages else [Message(content="")]

    def _extract_output_messages_from_chat(self, response: Any) -> List[Message]:
        """Extract output messages from llama-index ChatResponse."""
        output_messages: List[Message] = []

        # llama-index ChatResponse has a 'message' attribute which is a ChatMessage
        message = _get_attr(response, "message", None)
        if message is not None:
            content = ""
            role = "assistant"

            if hasattr(message, "content"):
                content = message.content or ""
            elif hasattr(message, "blocks"):
                blocks = message.blocks or []
                content_parts = []
                for block in blocks:
                    if hasattr(block, "text"):
                        content_parts.append(block.text)
                    elif isinstance(block, str):
                        content_parts.append(block)
                content = " ".join(content_parts)

            if hasattr(message, "role"):
                role_val = message.role
                if hasattr(role_val, "value"):
                    role = str(role_val.value)
                else:
                    role = str(role_val)

            output_messages.append(Message(content=str(content), role=role))

        return output_messages if output_messages else [Message(content="")]

    def _extract_output_messages_from_completion(self, response: Any) -> List[Message]:
        """Extract output messages from llama-index CompletionResponse."""
        output_messages: List[Message] = []

        # llama-index CompletionResponse has a 'text' attribute
        text = _get_attr(response, "text", None)
        if text is not None:
            output_messages.append(Message(content=str(text), role="assistant"))

        return output_messages if output_messages else [Message(content="")]

    def _extract_usage_from_response(self, response: Any) -> Dict[str, int]:
        """Extract token usage from response.

        llama-index stores usage in several possible locations:
        - response.additional_kwargs (direct LLM responses)
        - response.message.additional_kwargs (ChatResponse from CustomLLM.chat() -> complete() flow)
        - response.raw.usage (raw provider responses)
        """
        metrics = {}

        # Try additional_kwargs first (common location for direct LLM responses)
        additional_kwargs = _get_attr(response, "additional_kwargs", {}) or {}
        usage = additional_kwargs.get("usage", None)

        # Try response.message.additional_kwargs (ChatResponse converted from CompletionResponse)
        # When CustomLLM.chat() calls complete(), the completion_response_to_chat_response()
        # puts additional_kwargs into message.additional_kwargs, not response.additional_kwargs
        if not usage:
            message = _get_attr(response, "message", None)
            if message:
                message_kwargs = _get_attr(message, "additional_kwargs", {}) or {}
                usage = message_kwargs.get("usage", None)

        # Try raw response if not in additional_kwargs
        if not usage:
            raw = _get_attr(response, "raw", None)
            if raw:
                usage = _get_attr(raw, "usage", None)

        if usage:
            # Handle different token field names
            input_tokens = _get_attr(usage, "prompt_tokens", None) or _get_attr(usage, "input_tokens", None)
            output_tokens = _get_attr(usage, "completion_tokens", None) or _get_attr(usage, "output_tokens", None)
            total_tokens = _get_attr(usage, "total_tokens", None)

            if input_tokens is not None:
                metrics[INPUT_TOKENS_METRIC_KEY] = int(input_tokens)
            if output_tokens is not None:
                metrics[OUTPUT_TOKENS_METRIC_KEY] = int(output_tokens)
            if input_tokens is not None and output_tokens is not None:
                metrics[TOTAL_TOKENS_METRIC_KEY] = (
                    int(total_tokens) if total_tokens else int(input_tokens) + int(output_tokens)
                )

        return metrics
