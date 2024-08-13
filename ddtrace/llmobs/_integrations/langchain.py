import json
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Union

from ddtrace import config
from ddtrace._trace.span import Span
from ddtrace.constants import ERROR_TYPE
from ddtrace.internal.logger import get_logger
from ddtrace.llmobs import LLMObs
from ddtrace.llmobs._constants import INPUT_DOCUMENTS
from ddtrace.llmobs._constants import INPUT_MESSAGES
from ddtrace.llmobs._constants import INPUT_VALUE
from ddtrace.llmobs._constants import METADATA
from ddtrace.llmobs._constants import METRICS
from ddtrace.llmobs._constants import MODEL_NAME
from ddtrace.llmobs._constants import MODEL_PROVIDER
from ddtrace.llmobs._constants import OUTPUT_MESSAGES
from ddtrace.llmobs._constants import OUTPUT_VALUE
from ddtrace.llmobs._constants import SPAN_KIND

from ..utils import Document
from .base import BaseLLMIntegration


log = get_logger(__name__)


API_KEY = "langchain.request.api_key"
MODEL = "langchain.request.model"
PROVIDER = "langchain.request.provider"
TOTAL_COST = "langchain.tokens.total_cost"
TYPE = "langchain.request.type"

ANTHROPIC_PROVIDER_NAME = "anthropic"
BEDROCK_PROVIDER_NAME = "amazon_bedrock"
OPENAI_PROVIDER_NAME = "openai"

ROLE_MAPPING = {
    "human": "user",
    "ai": "assistant",
    "system": "system",
}

SUPPORTED_OPERATIONS = ["llm", "chat", "chain", "embedding"]


class LangChainIntegration(BaseLLMIntegration):
    _integration_name = "langchain"

    def llmobs_set_tags(
        self,
        operation: str,  # oneof "llm","chat","chain","embedding"
        span: Span,
        inputs: Any,
        response: Any = None,
        error: bool = False,
    ) -> None:
        """Sets meta tags and metrics for span events to be sent to LLMObs."""
        if not self.llmobs_enabled:
            return
        if operation not in SUPPORTED_OPERATIONS:
            log.warning("Unsupported operation : %s", operation)
            return

        model_provider = span.get_tag(PROVIDER)
        self._llmobs_set_metadata(span, model_provider)

        is_workflow = False

        if model_provider:
            llmobs_integration = "custom"
            if model_provider.startswith(BEDROCK_PROVIDER_NAME):
                llmobs_integration = "bedrock"
            elif model_provider.startswith(OPENAI_PROVIDER_NAME):
                llmobs_integration = "openai"
            elif operation == "chat" and model_provider.startswith(ANTHROPIC_PROVIDER_NAME):
                llmobs_integration = "anthropic"

            is_workflow = LLMObs._integration_is_enabled(llmobs_integration)

        if operation == "llm":
            self._llmobs_set_meta_tags_from_llm(span, inputs, response, error, is_workflow=is_workflow)
        elif operation == "chat":
            self._llmobs_set_meta_tags_from_chat_model(span, inputs, response, error, is_workflow=is_workflow)
        elif operation == "chain":
            self._llmobs_set_meta_tags_from_chain(span, inputs, response, error)
        elif operation == "embedding":
            self._llmobs_set_meta_tags_from_embedding(span, inputs, response, error, is_workflow=is_workflow)
        span.set_tag_str(METRICS, json.dumps({}))

    def _llmobs_set_metadata(self, span: Span, model_provider: Optional[str] = None) -> None:
        if not model_provider:
            return

        metadata = {}
        temperature = span.get_tag(f"langchain.request.{model_provider}.parameters.temperature") or span.get_tag(
            f"langchain.request.{model_provider}.parameters.model_kwargs.temperature"
        )  # huggingface
        max_tokens = (
            span.get_tag(f"langchain.request.{model_provider}.parameters.max_tokens")
            or span.get_tag(f"langchain.request.{model_provider}.parameters.maxTokens")  # ai21
            or span.get_tag(f"langchain.request.{model_provider}.parameters.model_kwargs.max_tokens")  # huggingface
        )

        if temperature is not None and temperature != "None":
            metadata["temperature"] = float(temperature)
        if max_tokens is not None and max_tokens != "None":
            metadata["max_tokens"] = int(max_tokens)
        if metadata:
            span.set_tag_str(METADATA, json.dumps(metadata))

    def _llmobs_set_meta_tags_from_llm(
        self, span: Span, prompts: List[Any], completions: Any, err: bool = False, is_workflow: bool = False
    ) -> None:
        span.set_tag_str(SPAN_KIND, "workflow" if is_workflow else "llm")
        span.set_tag_str(MODEL_NAME, span.get_tag(MODEL) or "")
        span.set_tag_str(MODEL_PROVIDER, span.get_tag(PROVIDER) or "")

        input_tag_key = INPUT_VALUE if is_workflow else INPUT_MESSAGES
        output_tag_key = OUTPUT_VALUE if is_workflow else OUTPUT_MESSAGES

        if isinstance(prompts, str):
            prompts = [prompts]

        span.set_tag_str(input_tag_key, json.dumps([{"content": str(prompt)} for prompt in prompts]))

        message_content = [{"content": ""}]
        if not err:
            message_content = [{"content": completion[0].text} for completion in completions.generations]
        span.set_tag_str(output_tag_key, json.dumps(message_content))

    def _llmobs_set_meta_tags_from_chat_model(
        self,
        span: Span,
        chat_messages: List[List[Any]],
        chat_completions: Any,
        err: bool = False,
        is_workflow: bool = False,
    ) -> None:
        span.set_tag_str(SPAN_KIND, "workflow" if is_workflow else "llm")
        span.set_tag_str(MODEL_NAME, span.get_tag(MODEL) or "")
        span.set_tag_str(MODEL_PROVIDER, span.get_tag(PROVIDER) or "")

        input_tag_key = INPUT_VALUE if is_workflow else INPUT_MESSAGES
        output_tag_key = OUTPUT_VALUE if is_workflow else OUTPUT_MESSAGES

        input_messages = []
        for message_set in chat_messages:
            for message in message_set:
                content = message.get("content", "") if isinstance(message, dict) else getattr(message, "content", "")
                input_messages.append(
                    {
                        "content": str(content),
                        "role": getattr(message, "role", ROLE_MAPPING.get(message.type, "")),
                    }
                )
        span.set_tag_str(input_tag_key, json.dumps(input_messages))

        output_messages = [{"content": ""}]
        if not err:
            output_messages = []
            for message_set in chat_completions.generations:
                for chat_completion in message_set:
                    chat_completion_msg = chat_completion.message
                    role = getattr(chat_completion_msg, "role", ROLE_MAPPING.get(chat_completion_msg.type, ""))
                    output_messages.append(
                        {
                            "content": str(chat_completion.text),
                            "role": role,
                        }
                    )
        span.set_tag_str(output_tag_key, json.dumps(output_messages))

    def _llmobs_set_meta_tags_from_chain(
        self,
        span: Span,
        inputs: Union[str, Dict[str, Any], List[Union[str, Dict[str, Any]]]],
        outputs: Any,
        error: bool = False,
    ) -> None:
        span.set_tag_str(SPAN_KIND, "workflow")

        if inputs is not None:
            try:
                formatted_inputs = self.format_io(inputs)
                if isinstance(formatted_inputs, str):
                    span.set_tag_str(INPUT_VALUE, formatted_inputs)
                else:
                    span.set_tag_str(INPUT_VALUE, json.dumps(self.format_io(inputs)))
            except TypeError:
                log.warning("Failed to serialize chain input data to JSON")
        if error:
            span.set_tag_str(OUTPUT_VALUE, "")
        elif outputs is not None:
            try:
                formatted_outputs = self.format_io(outputs)
                if isinstance(formatted_outputs, str):
                    span.set_tag_str(OUTPUT_VALUE, formatted_outputs)
                else:
                    span.set_tag_str(OUTPUT_VALUE, json.dumps(self.format_io(outputs)))
            except TypeError:
                log.warning("Failed to serialize chain output data to JSON")

    def _llmobs_set_meta_tags_from_embedding(
        self,
        span: Span,
        input_texts: Union[str, List[str]],
        output_embedding: Union[List[float], List[List[float]], None],
        error: bool = False,
        is_workflow: bool = False,
    ) -> None:
        span.set_tag_str(SPAN_KIND, "workflow" if is_workflow else "embedding")
        span.set_tag_str(MODEL_NAME, span.get_tag(MODEL) or "")
        span.set_tag_str(MODEL_PROVIDER, span.get_tag(PROVIDER) or "")

        input_tag_key = INPUT_VALUE if is_workflow else INPUT_DOCUMENTS
        output_tag_key = OUTPUT_VALUE

        output_values: Any

        try:
            if isinstance(input_texts, str) or (
                isinstance(input_texts, list) and all(isinstance(text, str) for text in input_texts)
            ):
                if is_workflow:
                    formatted_inputs = self.format_io(input_texts)
                    formatted_str = (
                        formatted_inputs
                        if isinstance(formatted_inputs, str)
                        else json.dumps(self.format_io(input_texts))
                    )
                    span.set_tag_str(input_tag_key, formatted_str)
                else:
                    if isinstance(input_texts, str):
                        input_texts = [input_texts]
                    input_documents = [Document(text=str(doc)) for doc in input_texts]
                    span.set_tag_str(input_tag_key, json.dumps(input_documents))
        except TypeError:
            log.warning("Failed to serialize embedding input data to JSON")
        if error:
            span.set_tag_str(output_tag_key, "")
        elif output_embedding is not None:
            try:
                if isinstance(output_embedding[0], float):
                    # single embedding through embed_query
                    output_values = [output_embedding]
                    embeddings_count = 1
                else:
                    # multiple embeddings through embed_documents
                    output_values = output_embedding
                    embeddings_count = len(output_embedding)
                embedding_dim = len(output_values[0])
                span.set_tag_str(
                    output_tag_key,
                    "[{} embedding(s) returned with size {}]".format(embeddings_count, embedding_dim),
                )
            except (TypeError, IndexError):
                log.warning("Failed to write output vectors", output_embedding)

    def _set_base_span_tags(  # type: ignore[override]
        self,
        span: Span,
        interface_type: str = "",
        provider: Optional[str] = None,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
    ) -> None:
        """Set base level tags that should be present on all LangChain spans (if they are not None)."""
        span.set_tag_str(TYPE, interface_type)
        if provider is not None:
            span.set_tag_str(PROVIDER, provider)
        if model is not None:
            span.set_tag_str(MODEL, model)
        if api_key is not None:
            if len(api_key) >= 4:
                span.set_tag_str(API_KEY, "...%s" % str(api_key[-4:]))
            else:
                span.set_tag_str(API_KEY, api_key)

    @classmethod
    def _logs_tags(cls, span: Span) -> str:
        api_key = span.get_tag(API_KEY) or ""
        tags = "env:%s,version:%s,%s:%s,%s:%s,%s:%s,%s:%s" % (  # noqa: E501
            (config.env or ""),
            (config.version or ""),
            PROVIDER,
            (span.get_tag(PROVIDER) or ""),
            MODEL,
            (span.get_tag(MODEL) or ""),
            TYPE,
            (span.get_tag(TYPE) or ""),
            API_KEY,
            api_key,
        )
        return tags

    @classmethod
    def _metrics_tags(cls, span: Span) -> List[str]:
        provider = span.get_tag(PROVIDER) or ""
        api_key = span.get_tag(API_KEY) or ""
        tags = [
            "version:%s" % (config.version or ""),
            "env:%s" % (config.env or ""),
            "service:%s" % (span.service or ""),
            "%s:%s" % (PROVIDER, provider),
            "%s:%s" % (MODEL, span.get_tag(MODEL) or ""),
            "%s:%s" % (TYPE, span.get_tag(TYPE) or ""),
            "%s:%s" % (API_KEY, api_key),
            "error:%d" % span.error,
        ]
        err_type = span.get_tag(ERROR_TYPE)
        if err_type:
            tags.append("%s:%s" % (ERROR_TYPE, err_type))
        return tags

    def record_usage(self, span: Span, usage: Dict[str, Any]) -> None:
        if not usage or self.metrics_enabled is False:
            return
        for token_type in ("prompt", "completion", "total"):
            num_tokens = usage.get("token_usage", {}).get(token_type + "_tokens")
            if not num_tokens:
                continue
            self.metric(span, "dist", "tokens.%s" % token_type, num_tokens)
        total_cost = span.get_metric(TOTAL_COST)
        if total_cost:
            self.metric(span, "incr", "tokens.total_cost", total_cost)

    def format_io(
        self,
        messages,
    ):
        """
        Formats input and output messages for serialization to JSON.
        Specifically, makes sure that any schema messages are converted to strings appropriately.
        """
        if isinstance(messages, dict):
            formatted = {}
            for key, value in messages.items():
                formatted[key] = self.format_io(value)
            return formatted
        if isinstance(messages, list):
            return [self.format_io(message) for message in messages]
        return self.get_content_from_message(messages)

    def get_content_from_message(self, message) -> str:
        """
        Attempts to extract the content and role from a message (AIMessage, HumanMessage, SystemMessage) object.
        """
        if isinstance(message, str):
            return message
        try:
            content = getattr(message, "__dict__", {}).get("content", str(message))
            role = getattr(message, "role", ROLE_MAPPING.get(getattr(message, "type"), ""))
            return (role, content) if role else content
        except AttributeError:
            return str(message)
