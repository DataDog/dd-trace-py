from typing import Any
from typing import Optional

from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._constants import INPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import OUTPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import TOTAL_TOKENS_METRIC_KEY
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.llmobs._utils import _annotate_llmobs_span_data
from ddtrace.llmobs._utils import _get_attr
from ddtrace.llmobs.types import Document
from ddtrace.llmobs.types import Message
from ddtrace.trace import Span


log = get_logger(__name__)


MODEL = "llama_index.request.model"
PROVIDER = "llama_index.request.provider"


class LlamaIndexIntegration(BaseLLMIntegration):
    _integration_name = "llama_index"

    def _set_base_span_tags(
        self,
        span: Span,
        model: Optional[str] = None,
        provider: Optional[str] = None,
        **kwargs: dict[str, Any],
    ) -> None:
        if model is not None:
            span._set_attribute(MODEL, model)
        if provider is not None:
            span._set_attribute(PROVIDER, provider)

    def _llmobs_set_tags(
        self,
        span: Span,
        args: list[Any],
        kwargs: dict[str, Any],
        response: Optional[Any] = None,
        operation: str = "",
    ) -> None:
        """Dispatch to the operation-specific LLMObs tag setter.

        Called by ``LlmTracingSubscriber.on_ended`` after the traced method
        returns.  ``kwargs`` are the request kwargs built by the corresponding
        ``build_*_request_kwargs`` helper in ``_utils.py`` — keys vary by
        operation (e.g. ``messages`` for chat, ``query_str`` for retrieval).
        ``response`` is the raw return value from the LlamaIndex method.
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
            output_value = str(_get_attr(response, "response", None) or "")

        _annotate_llmobs_span_data(span, kind="workflow", input_value=input_value, output_value=output_value)

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

        output_value = str(response) if not span.error and response is not None else ""

        _annotate_llmobs_span_data(span, kind="agent", input_value=input_value, output_value=output_value)

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
        output_value = str(response) if not span.error and response is not None else ""

        _annotate_llmobs_span_data(span, kind="tool", input_value=tool_name, output_value=output_value)

    def _llmobs_set_embedding_tags(
        self,
        span: Span,
        kwargs: dict[str, Any],
        response: Optional[Any] = None,
    ) -> None:
        """Set LLMObs tags for embedding spans.

        Expected ``kwargs`` keys: ``query`` (or ``[N texts]`` summary for batch).
        Model name is read from the span tag set by ``_set_base_span_tags``.
        Response is a list of floats (single query) or list of lists (batch).
        """
        input_value = kwargs.get("query", "")
        input_documents = [Document(text=str(input_value))]

        output_value = None
        if not span.error and response is not None:
            if isinstance(response, list) and response:
                if isinstance(response[0], list):
                    # Batch: list of embedding vectors
                    embedding_count = len(response)
                    embedding_dim = len(response[0]) if response[0] else 0
                else:
                    # Single query: one embedding vector
                    embedding_count = 1
                    embedding_dim = len(response)
                output_value = "[{} embedding(s) returned with size {}]".format(embedding_count, embedding_dim)

        _annotate_llmobs_span_data(
            span,
            kind="embedding",
            model_name=span.get_tag(MODEL) or "",
            model_provider=span.get_tag(PROVIDER) or "",
            input_documents=input_documents,
            output_value=output_value,
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

        output_documents = None
        if not span.error and response is not None:
            documents: list[Document] = []
            for node_with_score in response:
                text = str(_get_attr(node_with_score, "text", "") or "")
                score = _get_attr(node_with_score, "score", None)
                doc = Document(text=text)
                if score is not None:
                    try:
                        doc["score"] = float(score)
                    except (TypeError, ValueError):
                        log.debug("Failed to convert score to float: %s", score)
                node_id = _get_attr(node_with_score, "node_id", None)
                if node_id is not None:
                    doc["id"] = str(node_id)
                documents.append(doc)
            if documents:
                output_documents = documents

        _annotate_llmobs_span_data(span, kind="retrieval", input_value=input_value, output_documents=output_documents)

    def _llmobs_set_llm_tags(
        self,
        span: Span,
        kwargs: dict[str, Any],
        response: Optional[Any] = None,
    ) -> None:
        """Set LLMObs tags for LLM (chat/completion) spans.

        Chat vs completion is auto-detected: presence of ``messages`` in kwargs
        indicates chat (from ``build_chat_request_kwargs``), otherwise completion
        (from ``build_complete_request_kwargs``).

        For chat: response is a ``ChatResponse`` with ``.message.content``.
        For completion: response is a ``CompletionResponse`` with ``.text``.
        Both have ``.raw.usage`` for token metrics (provider-dependent structure).
        """
        metadata = {}
        if kwargs.get("max_tokens") is not None:
            metadata["max_tokens"] = kwargs["max_tokens"]
        if kwargs.get("temperature") is not None:
            metadata["temperature"] = kwargs["temperature"]

        is_chat = "messages" in kwargs

        input_messages = self._extract_input_messages(kwargs, is_chat)

        output_messages: list[Message] = [Message(content="")]
        metrics = {}
        if not span.error and response is not None:
            output_messages = self._extract_output_messages(response, is_chat)
            metrics = self._extract_usage(response)

        _annotate_llmobs_span_data(
            span,
            kind="llm",
            model_name=span.get_tag(MODEL) or "",
            model_provider=span.get_tag(PROVIDER) or "",
            input_messages=input_messages,
            metadata=metadata,
            output_messages=output_messages,
            metrics=metrics,
        )

    def _set_apm_shadow_tags(self, span, args, kwargs, response=None, operation=""):
        span_kind_map = {
            "": "llm",
            "llm": "llm",
            "query": "workflow",
            "retrieval": "retrieval",
            "embedding": "embedding",
            "agent": "agent",
            "tool": "tool",
        }
        span_kind = span_kind_map.get(operation, "workflow")
        metrics = {}
        if operation in ("", "llm") and not span.error and response is not None:
            metrics = self._extract_usage(response)
        self._apply_shadow_metrics(
            span,
            metrics,
            span_kind,
            model_name=span.get_tag(MODEL),
            model_provider=span.get_tag(PROVIDER),
            llmobs_enabled=self.llmobs_enabled,
        )

    def _extract_input_messages(self, kwargs: dict[str, Any], is_chat: bool) -> list[Message]:
        """Extract input messages for LLM spans (chat or completion)."""
        if is_chat:
            input_messages: list[Message] = []
            for msg in kwargs.get("messages", []):
                role = _get_attr(msg, "role", "user")
                content = _get_attr(msg, "content", "")
                role = _get_attr(role, "value", role)
                input_messages.append(Message(content=str(content), role=str(role)))
            return input_messages
        prompt = kwargs.get("prompt", "")
        return [Message(content=str(prompt), role="user")]

    def _extract_output_messages(self, response: Any, is_chat: bool) -> list[Message]:
        """Extract output messages for LLM spans (chat or completion).

        For chat: response is a ChatResponse with .message.content.
        For completion: response is a CompletionResponse with .text, or a plain string
        (predict() returns a plain string while complete() returns a CompletionResponse).
        """
        if is_chat:
            message = _get_attr(response, "message", None)
            if message is None:
                return [Message(content="")]
            role = _get_attr(message, "role", "assistant")
            content = _get_attr(message, "content", "")
            role = _get_attr(role, "value", role)
            return [Message(content=str(content), role=str(role))]
        text = _get_attr(response, "text", None)
        if text is None:
            text = str(response) if response else ""
        return [Message(content=str(text), role="assistant")]

    def _extract_usage(self, response: Any) -> dict[str, Any]:
        """Extract token usage metrics from a ``ChatResponse`` or ``CompletionResponse``.

        LlamaIndex responses expose ``.raw`` which is the provider's original
        response object (e.g. an OpenAI ``ChatCompletion``).  Usage is found at
        ``response.raw.usage`` — the structure varies by provider:
        - OpenAI: ``prompt_tokens``, ``completion_tokens``, ``total_tokens``
        - Anthropic: ``input_tokens``, ``output_tokens``
        """
        metrics: dict[str, Any] = {}
        raw = _get_attr(response, "raw", None)
        if raw is None:
            return metrics

        usage = _get_attr(raw, "usage", None)
        if usage is None:
            return metrics

        input_tokens = _get_attr(usage, "prompt_tokens", None)
        if input_tokens is None:
            input_tokens = _get_attr(usage, "input_tokens", None)
        output_tokens = _get_attr(usage, "completion_tokens", None)
        if output_tokens is None:
            output_tokens = _get_attr(usage, "output_tokens", None)
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
