from typing import Any
from typing import Optional

from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._constants import INPUT_MESSAGES
from ddtrace.llmobs._constants import INPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import INPUT_VALUE
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
from ddtrace.llmobs.types import Message
from ddtrace.trace import Span


log = get_logger(__name__)


MODEL = "llama_index.request.model"


class LlamaIndexIntegration(BaseLLMIntegration):
    _integration_name = "llama_index"

    def _set_base_span_tags(
        self,
        span: Span,
        model: Optional[str] = None,
        **kwargs: dict[str, Any],
    ) -> None:
        if model is not None:
            span._set_attribute(MODEL, model)

    def _llmobs_set_tags(
        self,
        span: Span,
        args: list[Any],
        kwargs: dict[str, Any],
        response: Optional[Any] = None,
        operation: str = "",
    ) -> None:
        """Route to the appropriate tag-setter based on the operation type.

        ``kwargs`` contains the request kwargs built by the corresponding
        ``build_*_request_kwargs`` helper (e.g. messages, prompt, model, query_str).
        """
        if operation == "query":
            self._llmobs_set_query_tags(span, kwargs, response)
            return
        if operation == "retrieval":
            self._llmobs_set_retrieval_tags(span, kwargs, response)
            return
        if operation == "embedding":
            self._llmobs_set_embedding_tags(span, kwargs, response)
            return
        if operation == "agent":
            self._llmobs_set_agent_tags(span, kwargs, response)
            return
        if operation == "tool":
            self._llmobs_set_tool_tags(span, kwargs, response)
            return

        self._llmobs_set_llm_tags(span, kwargs, response)

    def _llmobs_set_query_tags(
        self,
        span: Span,
        kwargs: dict[str, Any],
        response: Optional[Any] = None,
    ) -> None:
        """Set LLMObs tags for query engine (workflow) spans.

        Expected ``kwargs`` keys: ``query_str``.
        Response is a LlamaIndex ``Response`` object with ``.response`` text.
        """
        input_value = kwargs.get("query_str", "")

        output_value = ""
        if not span.error and response is not None:
            # QueryEngine responses have a .response attribute with the text output
            output_value = str(_get_attr(response, "response", "") or "")

        span._set_ctx_items(
            {
                SPAN_KIND: "workflow",
                INPUT_VALUE: input_value,
                OUTPUT_VALUE: output_value,
            }
        )

    def _llmobs_set_agent_tags(
        self,
        span: Span,
        kwargs: dict[str, Any],
        response: Optional[Any] = None,
    ) -> None:
        """Set LLMObs tags for agent (workflow) spans.

        Expected ``kwargs`` keys: ``input``.
        """
        input_value = kwargs.get("input", "")

        span._set_ctx_items(
            {
                SPAN_KIND: "agent",
                INPUT_VALUE: input_value,
            }
        )

    def _llmobs_set_tool_tags(
        self,
        span: Span,
        kwargs: dict[str, Any],
        response: Optional[Any] = None,
    ) -> None:
        """Set LLMObs tags for tool call spans.

        Expected ``kwargs`` keys: ``tool_name``.
        """
        tool_name = kwargs.get("tool_name", "")

        span._set_ctx_items(
            {
                SPAN_KIND: "tool",
                INPUT_VALUE: tool_name,
            }
        )

    def _llmobs_set_embedding_tags(
        self,
        span: Span,
        kwargs: dict[str, Any],
        response: Optional[Any] = None,
    ) -> None:
        """Set LLMObs tags for embedding spans.

        Expected ``kwargs`` keys: ``query`` (or ``[N texts]`` summary for batch).
        Model name is read from the span tag set by ``_set_base_span_tags``.
        """
        input_value = kwargs.get("query", "")

        span._set_ctx_items(
            {
                SPAN_KIND: "embedding",
                INPUT_VALUE: input_value,
                MODEL_NAME: span.get_tag(MODEL) or "",
                MODEL_PROVIDER: "llama_index",
            }
        )

    def _llmobs_set_retrieval_tags(
        self,
        span: Span,
        kwargs: dict[str, Any],
        response: Optional[Any] = None,
    ) -> None:
        """Set LLMObs tags for retriever (retrieval) spans.

        Expected ``kwargs`` keys: ``query_str``.
        Response is a list of ``NodeWithScore`` objects with ``.text`` and ``.score``.
        """
        input_value = kwargs.get("query_str", "")

        output_value = ""
        if not span.error and response is not None:
            # retrieve() returns a list of NodeWithScore objects
            try:
                documents = []
                for node_with_score in response:
                    text = str(_get_attr(node_with_score, "text", "") or "")
                    score = _get_attr(node_with_score, "score", None)
                    doc = {"text": text}
                    if score is not None:
                        doc["score"] = score
                    documents.append(doc)
                output_value = str(documents)
            except Exception:
                log.debug("Failed to extract retriever output", exc_info=True)

        span._set_ctx_items(
            {
                SPAN_KIND: "retrieval",
                INPUT_VALUE: input_value,
                OUTPUT_VALUE: output_value,
            }
        )

    def _llmobs_set_llm_tags(
        self,
        span: Span,
        kwargs: dict[str, Any],
        response: Optional[Any] = None,
    ) -> None:
        """Set LLMObs tags for LLM (chat/completion) spans.

        Expected ``kwargs`` keys: ``messages`` (chat) or ``prompt`` (completion),
        plus optional ``max_tokens`` and ``temperature``.
        Chat vs. completion is determined by the ``_dd_is_chat`` context item
        set by the tracing subscriber.
        """
        parameters = {}
        if kwargs.get("max_tokens") is not None:
            parameters["max_tokens"] = kwargs["max_tokens"]
        if kwargs.get("temperature") is not None:
            parameters["temperature"] = kwargs["temperature"]

        is_chat = span._get_ctx_item("_dd_is_chat") or False

        if is_chat:
            input_messages = self._extract_chat_input_messages(kwargs)
        else:
            input_messages = self._extract_completion_input_messages(kwargs)

        output_messages: list[Message] = [Message(content="")]
        if not span.error and response is not None:
            if is_chat:
                output_messages = self._extract_chat_output_messages(response)
            else:
                output_messages = self._extract_completion_output_messages(response)

        metrics = {}
        if not span.error and response is not None:
            metrics = self._extract_usage(response)

        span._set_ctx_items(
            {
                SPAN_KIND: "llm",
                MODEL_NAME: span.get_tag(MODEL) or "",
                MODEL_PROVIDER: "llama_index",
                INPUT_MESSAGES: input_messages,
                METADATA: parameters,
                OUTPUT_MESSAGES: output_messages,
                METRICS: metrics,
            }
        )

    def _extract_chat_input_messages(self, kwargs: dict[str, Any]) -> list[Message]:
        """Extract input messages from a chat request.

        LlamaIndex chat methods receive messages as a sequence of ChatMessage objects.
        """
        messages = kwargs.get("messages", [])
        input_messages: list[Message] = []

        for msg in messages:
            role = _get_attr(msg, "role", "user")
            content = _get_attr(msg, "content", "")

            # MessageRole is an enum - get the value
            if hasattr(role, "value"):
                role = role.value

            input_messages.append(Message(content=str(content), role=str(role)))

        return input_messages

    def _extract_completion_input_messages(self, kwargs: dict[str, Any]) -> list[Message]:
        """Extract input from a completion/predict request as a single user message."""
        prompt = kwargs.get("prompt", "")
        return [Message(content=str(prompt), role="user")]

    def _extract_chat_output_messages(self, response: Any) -> list[Message]:
        """Extract the assistant message from a ChatResponse.

        Returns a single-element list. Falls back to empty content if the
        response has no ``message`` attribute (e.g. on error).
        """
        message = _get_attr(response, "message", None)
        if message is None:
            return [Message(content="")]

        role = _get_attr(message, "role", "assistant")
        content = _get_attr(message, "content", "")

        if hasattr(role, "value"):
            role = role.value

        return [Message(content=str(content), role=str(role))]

    def _extract_completion_output_messages(self, response: Any) -> list[Message]:
        """Extract output text from a CompletionResponse or plain string.

        ``predict()`` returns a plain string while ``complete()`` returns a
        CompletionResponse with ``.text``.  Falls back to ``str(response)``
        when no ``.text`` attribute is found.
        """
        text = _get_attr(response, "text", None)
        if text is None:
            text = str(response) if response else ""
        return [Message(content=str(text), role="assistant")]

    def _extract_usage(self, response: Any) -> dict[str, Any]:
        """Extract token usage from the response.

        LlamaIndex responses have a `raw` dict that contains the provider's raw response,
        which typically includes usage information.
        """
        metrics: dict[str, Any] = {}
        raw = _get_attr(response, "raw", None)
        if raw is None:
            return metrics

        # Different providers structure usage differently in the raw response.
        # OpenAI: raw.usage.prompt_tokens / completion_tokens / total_tokens
        # Or: raw["usage"]["prompt_tokens"] etc.
        usage = None
        if isinstance(raw, dict):
            usage = raw.get("usage")
        else:
            usage = _get_attr(raw, "usage", None)

        if usage is None:
            return metrics

        input_tokens = None
        output_tokens = None

        if isinstance(usage, dict):
            # OpenAI-style: prompt_tokens, completion_tokens
            input_tokens = usage.get("prompt_tokens") or usage.get("input_tokens")
            output_tokens = usage.get("completion_tokens") or usage.get("output_tokens")
            total_tokens = usage.get("total_tokens")
        else:
            input_tokens = _get_attr(usage, "prompt_tokens", None) or _get_attr(usage, "input_tokens", None)
            output_tokens = _get_attr(usage, "completion_tokens", None) or _get_attr(usage, "output_tokens", None)
            total_tokens = _get_attr(usage, "total_tokens", None)

        if input_tokens is not None:
            metrics[INPUT_TOKENS_METRIC_KEY] = input_tokens
        if output_tokens is not None:
            metrics[OUTPUT_TOKENS_METRIC_KEY] = output_tokens

        if input_tokens is not None and output_tokens is not None:
            metrics[TOTAL_TOKENS_METRIC_KEY] = input_tokens + output_tokens
        elif total_tokens is not None:
            metrics[TOTAL_TOKENS_METRIC_KEY] = total_tokens

        return metrics
