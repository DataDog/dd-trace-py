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
from ddtrace.internal.utils import ArgumentError
from ddtrace.internal.utils import get_argument_value
from ddtrace.llmobs import LLMObs
from ddtrace.llmobs._constants import INPUT_DOCUMENTS
from ddtrace.llmobs._constants import INPUT_MESSAGES
from ddtrace.llmobs._constants import INPUT_VALUE
from ddtrace.llmobs._constants import METADATA
from ddtrace.llmobs._constants import METRICS
from ddtrace.llmobs._constants import MODEL_NAME
from ddtrace.llmobs._constants import MODEL_PROVIDER
from ddtrace.llmobs._constants import OUTPUT_DOCUMENTS
from ddtrace.llmobs._constants import OUTPUT_MESSAGES
from ddtrace.llmobs._constants import OUTPUT_VALUE
from ddtrace.llmobs._constants import SPAN_KIND
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.llmobs._utils import safe_json
from ddtrace.llmobs.utils import Document


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

SUPPORTED_OPERATIONS = ["llm", "chat", "chain", "embedding", "retrieval", "tool"]


class LangChainIntegration(BaseLLMIntegration):
    _integration_name = "langchain"

    def _llmobs_set_tags(
        self,
        span: Span,
        args: List[Any],
        kwargs: Dict[str, Any],
        response: Optional[Any] = None,
        operation: str = "",  # oneof "llm","chat","chain","embedding","retrieval","tool"
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
            self._llmobs_set_meta_tags_from_llm(span, args, kwargs, response, is_workflow=is_workflow)
        elif operation == "chat":
            self._llmobs_set_meta_tags_from_chat_model(span, args, kwargs, response, is_workflow=is_workflow)
        elif operation == "chain":
            self._llmobs_set_meta_tags_from_chain(span, args, kwargs, outputs=response)
        elif operation == "embedding":
            self._llmobs_set_meta_tags_from_embedding(span, args, kwargs, response, is_workflow=is_workflow)
        elif operation == "retrieval":
            self._llmobs_set_meta_tags_from_similarity_search(span, args, kwargs, response, is_workflow=is_workflow)
        elif operation == "tool":
            self._llmobs_set_meta_tags_from_tool(span, tool_inputs=kwargs, tool_output=response)

        span.set_tag_str(METRICS, safe_json({}))

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
            span.set_tag_str(METADATA, safe_json(metadata))

    def _llmobs_set_meta_tags_from_llm(
        self, span: Span, args: List[Any], kwargs: Dict[str, Any], completions: Any, is_workflow: bool = False
    ) -> None:
        span.set_tag_str(SPAN_KIND, "workflow" if is_workflow else "llm")
        span.set_tag_str(MODEL_NAME, span.get_tag(MODEL) or "")
        span.set_tag_str(MODEL_PROVIDER, span.get_tag(PROVIDER) or "")

        input_tag_key = INPUT_VALUE if is_workflow else INPUT_MESSAGES
        output_tag_key = OUTPUT_VALUE if is_workflow else OUTPUT_MESSAGES
        stream = span.get_tag("langchain.request.stream")

        prompts = get_argument_value(args, kwargs, 0, "input" if stream else "prompts")
        if isinstance(prompts, str) or not isinstance(prompts, list):
            prompts = [prompts]

        if stream:
            # chat and llm take the same input types for streamed calls
            span.set_tag_str(input_tag_key, safe_json(self._handle_stream_input_messages(prompts)))
        else:
            span.set_tag_str(input_tag_key, safe_json([{"content": str(prompt)} for prompt in prompts]))

        if span.error:
            span.set_tag_str(output_tag_key, safe_json([{"content": ""}]))
            return
        if stream:
            message_content = [{"content": completions}]  # single completion for streams
        else:
            message_content = [{"content": completion[0].text} for completion in completions.generations]
        span.set_tag_str(output_tag_key, safe_json(message_content))

    def _llmobs_set_meta_tags_from_chat_model(
        self,
        span: Span,
        args: List[Any],
        kwargs: Dict[str, Any],
        chat_completions: Any,
        is_workflow: bool = False,
    ) -> None:
        span.set_tag_str(SPAN_KIND, "workflow" if is_workflow else "llm")
        span.set_tag_str(MODEL_NAME, span.get_tag(MODEL) or "")
        span.set_tag_str(MODEL_PROVIDER, span.get_tag(PROVIDER) or "")

        input_tag_key = INPUT_VALUE if is_workflow else INPUT_MESSAGES
        output_tag_key = OUTPUT_VALUE if is_workflow else OUTPUT_MESSAGES
        stream = span.get_tag("langchain.request.stream")

        input_messages = []
        if stream:
            chat_messages = get_argument_value(args, kwargs, 0, "input")
            input_messages = self._handle_stream_input_messages(chat_messages)
        else:
            chat_messages = get_argument_value(args, kwargs, 0, "messages", optional=True) or []
            if not isinstance(chat_messages, list):
                chat_messages = [chat_messages]
            for message_set in chat_messages:
                for message in message_set:
                    content = (
                        message.get("content", "") if isinstance(message, dict) else getattr(message, "content", "")
                    )
                    role = getattr(message, "role", ROLE_MAPPING.get(message.type, ""))
                    input_messages.append({"content": str(content), "role": str(role)})
        span.set_tag_str(input_tag_key, safe_json(input_messages))

        if span.error:
            span.set_tag_str(output_tag_key, json.dumps([{"content": ""}]))
            return

        output_messages = []
        if stream:
            content = chat_completions.content
            role = chat_completions.__class__.__name__.replace("MessageChunk", "").lower()  # AIMessageChunk --> ai
            span.set_tag_str(output_tag_key, safe_json([{"content": content, "role": ROLE_MAPPING.get(role, "")}]))
            return

        for message_set in chat_completions.generations:
            for chat_completion in message_set:
                chat_completion_msg = chat_completion.message
                role = getattr(chat_completion_msg, "role", ROLE_MAPPING.get(chat_completion_msg.type, ""))
                output_message = {"content": str(chat_completion.text), "role": role}
                tool_calls_info = self._extract_tool_calls(chat_completion_msg)
                if tool_calls_info:
                    output_message["tool_calls"] = tool_calls_info
                output_messages.append(output_message)
        span.set_tag_str(output_tag_key, safe_json(output_messages))

    def _extract_tool_calls(self, chat_completion_msg: Any) -> List[Dict[str, Any]]:
        """Extracts tool calls from a langchain chat completion."""
        tool_calls = getattr(chat_completion_msg, "tool_calls", None)
        tool_calls_info = []
        if tool_calls:
            if not isinstance(tool_calls, list):
                tool_calls = [tool_calls]
            for tool_call in tool_calls:
                tool_call_info = {
                    "name": tool_call.get("name", ""),
                    "arguments": tool_call.get("args", {}),  # this is already a dict
                    "tool_id": tool_call.get("id", ""),
                }
                tool_calls_info.append(tool_call_info)
        return tool_calls_info

    def _handle_stream_input_messages(self, inputs):
        input_messages = []
        if hasattr(inputs, "to_messages"):  # isinstance(inputs, langchain_core.prompt_values.PromptValue)
            inputs = inputs.to_messages()
        elif not isinstance(inputs, list):
            inputs = [inputs]
        for inp in inputs:
            inp_message = {}
            content, role = None, None
            if isinstance(inp, dict):
                content = str(inp.get("content", ""))
                role = inp.get("role")
            elif hasattr(inp, "content"):  # isinstance(inp, langchain_core.messages.BaseMessage)
                content = str(inp.content)
                role = inp.__class__.__name__
            else:
                content = str(inp)

            inp_message["content"] = content
            if role is not None:
                inp_message["role"] = role
            input_messages.append(inp_message)

        return input_messages

    def _llmobs_set_meta_tags_from_chain(self, span: Span, args, kwargs, outputs: Any) -> None:
        span.set_tag_str(SPAN_KIND, "workflow")
        stream = span.get_tag("langchain.request.stream")
        if stream:
            inputs = get_argument_value(args, kwargs, 0, "input")
        else:
            inputs = kwargs
        if inputs is not None:
            formatted_inputs = self.format_io(inputs)
            span.set_tag_str(INPUT_VALUE, safe_json(formatted_inputs))
        if span.error or outputs is None:
            span.set_tag_str(OUTPUT_VALUE, "")
            return
        formatted_outputs = self.format_io(outputs)
        span.set_tag_str(OUTPUT_VALUE, safe_json(formatted_outputs))

    def _llmobs_set_meta_tags_from_embedding(
        self,
        span: Span,
        args: List[Any],
        kwargs: Dict[str, Any],
        output_embedding: Union[List[float], List[List[float]], None],
        is_workflow: bool = False,
    ) -> None:
        span.set_tag_str(SPAN_KIND, "workflow" if is_workflow else "embedding")
        span.set_tag_str(MODEL_NAME, span.get_tag(MODEL) or "")
        span.set_tag_str(MODEL_PROVIDER, span.get_tag(PROVIDER) or "")

        input_tag_key = INPUT_VALUE if is_workflow else INPUT_DOCUMENTS
        output_tag_key = OUTPUT_VALUE

        output_values: Any

        try:
            input_texts = get_argument_value(args, kwargs, 0, "texts")
        except ArgumentError:
            input_texts = get_argument_value(args, kwargs, 0, "text")
        try:
            if isinstance(input_texts, str) or (
                isinstance(input_texts, list) and all(isinstance(text, str) for text in input_texts)
            ):
                if is_workflow:
                    formatted_inputs = self.format_io(input_texts)
                    span.set_tag_str(input_tag_key, safe_json(formatted_inputs))
                else:
                    if isinstance(input_texts, str):
                        input_texts = [input_texts]
                    input_documents = [Document(text=str(doc)) for doc in input_texts]
                    span.set_tag_str(input_tag_key, safe_json(input_documents))
        except TypeError:
            log.warning("Failed to serialize embedding input data to JSON")
        if span.error or output_embedding is None:
            span.set_tag_str(output_tag_key, "")
            return
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

    def _llmobs_set_meta_tags_from_similarity_search(
        self,
        span: Span,
        args: List[Any],
        kwargs: Dict[str, Any],
        output_documents: Union[List[Any], None],
        is_workflow: bool = False,
    ) -> None:
        span.set_tag_str(SPAN_KIND, "workflow" if is_workflow else "retrieval")
        span.set_tag_str(MODEL_NAME, span.get_tag(MODEL) or "")
        span.set_tag_str(MODEL_PROVIDER, span.get_tag(PROVIDER) or "")

        input_query = get_argument_value(args, kwargs, 0, "query")
        if input_query is not None:
            formatted_inputs = self.format_io(input_query)
            span.set_tag_str(INPUT_VALUE, safe_json(formatted_inputs))
        if span.error or not output_documents or not isinstance(output_documents, list):
            span.set_tag_str(OUTPUT_VALUE, "")
            return
        if is_workflow:
            span.set_tag_str(OUTPUT_VALUE, "[{} document(s) retrieved]".format(len(output_documents)))
            return
        documents = []
        for d in output_documents:
            doc = Document(text=d.page_content)
            doc["id"] = getattr(d, "id", "")
            metadata = getattr(d, "metadata", {})
            doc["name"] = metadata.get("name", doc["id"])
            documents.append(doc)
        span.set_tag_str(OUTPUT_DOCUMENTS, safe_json(self.format_io(documents)))
        # we set the value as well to ensure that the UI would display it in case the span was the root
        span.set_tag_str(OUTPUT_VALUE, "[{} document(s) retrieved]".format(len(documents)))

    def _llmobs_set_meta_tags_from_tool(self, span: Span, tool_inputs: Dict[str, Any], tool_output: object) -> None:
        if span.get_tag(METADATA):
            metadata = json.loads(str(span.get_tag(METADATA)))
        else:
            metadata = {}

        span.set_tag_str(SPAN_KIND, "tool")
        if tool_inputs is not None:
            tool_input = tool_inputs.get("input")
            if tool_inputs.get("config"):
                metadata["tool_config"] = tool_inputs.get("config")
            if tool_inputs.get("info"):
                metadata["tool_info"] = tool_inputs.get("info")
            if metadata:
                span.set_tag_str(METADATA, safe_json(metadata))
            formatted_input = self.format_io(tool_input)
            span.set_tag_str(INPUT_VALUE, safe_json(formatted_input))
        if span.error or tool_output is None:
            span.set_tag_str(OUTPUT_VALUE, "")
            return
        formatted_outputs = self.format_io(tool_output)
        span.set_tag_str(OUTPUT_VALUE, safe_json(formatted_outputs))

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
