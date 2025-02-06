from collections import defaultdict
import json
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Set
from typing import Union
from weakref import WeakKeyDictionary

from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import ArgumentError
from ddtrace.internal.utils import get_argument_value
from ddtrace.llmobs import LLMObs
from ddtrace.llmobs._constants import INPUT_DOCUMENTS
from ddtrace.llmobs._constants import INPUT_MESSAGES
from ddtrace.llmobs._constants import INPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import INPUT_VALUE
from ddtrace.llmobs._constants import METADATA
from ddtrace.llmobs._constants import METRICS
from ddtrace.llmobs._constants import MODEL_NAME
from ddtrace.llmobs._constants import MODEL_PROVIDER
from ddtrace.llmobs._constants import OUTPUT_DOCUMENTS
from ddtrace.llmobs._constants import OUTPUT_MESSAGES
from ddtrace.llmobs._constants import OUTPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import OUTPUT_VALUE
from ddtrace.llmobs._constants import SPAN_KIND
from ddtrace.llmobs._constants import SPAN_LINKS
from ddtrace.llmobs._constants import TOTAL_TOKENS_METRIC_KEY
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.llmobs._integrations.utils import format_langchain_io
from ddtrace.llmobs._utils import _get_nearest_llmobs_ancestor
from ddtrace.llmobs.utils import Document
from ddtrace.trace import Span


log = get_logger(__name__)


API_KEY = "langchain.request.api_key"
MODEL = "langchain.request.model"
PROVIDER = "langchain.request.provider"
TOTAL_COST = "langchain.tokens.total_cost"
TYPE = "langchain.request.type"

ANTHROPIC_PROVIDER_NAME = "anthropic"
BEDROCK_PROVIDER_NAME = "amazon_bedrock"
OPENAI_PROVIDER_NAME = "openai"
VERTEXAI_PROVIDER_NAME = "vertexai"
GEMINI_PROVIDER_NAME = "google_palm"

ROLE_MAPPING = {
    "human": "user",
    "ai": "assistant",
    "system": "system",
}

SUPPORTED_OPERATIONS = ["llm", "chat", "chain", "embedding", "retrieval", "tool"]


def _extract_bound(instance):
    """
    Extracts the bound object from a RunnableBinding instance.
    LangChain will sometimes stick things we trace (chat models) in these bindings,
    and they will show up as steps in the chain instead of the model instance itself.
    """
    if hasattr(instance, "bound"):
        return instance.bound
    return instance


def _flattened_chain_steps(steps: List[Any], nested: bool = True) -> List[Any]:
    """
    Flattens the contents of a chain into non-RunnableBindings and non-RunnableParallel steps.
    RunnableParrallel steps are extracted and can either be nested into sublists or flattened.
    """
    flattened_steps = []
    for step in steps:
        step = _extract_bound(step)
        if hasattr(step, "steps__"):
            parallel_steps = getattr(step, "steps__", {})
            flattened_parallel_steps = []
            for parallel_step in parallel_steps.values():
                parallel_step = _extract_bound(parallel_step)
                flattened_parallel_steps.append(parallel_step)
            if nested:
                flattened_steps.append(flattened_parallel_steps)
            else:
                flattened_steps.extend(flattened_parallel_steps)
        else:
            flattened_steps.append(step)
    return flattened_steps


class LangChainIntegration(BaseLLMIntegration):
    _integration_name = "langchain"

    _chain_steps: Set[int] = set()
    """Set of instance ids that are steps in a chain."""

    _spans: Dict[int, Span] = {}
    """Maps instance ids to spans."""

    _instances: WeakKeyDictionary = WeakKeyDictionary()
    """Maps spans to instances."""

    def record_steps(self, instance, span):
        if not self.llmobs_enabled or not self.span_linking_enabled:
            return

        steps = getattr(instance, "steps", [])
        for step in _flattened_chain_steps(steps, nested=False):
            self._chain_steps.add(id(step))

        self.record_instance(instance, span)

    def record_instance(self, instance, span):
        if not self.llmobs_enabled or not self.span_linking_enabled:
            return

        instance = _extract_bound(instance)

        self._instances[span] = instance
        self._spans[id(instance)] = span

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

        if self.span_linking_enabled:
            self._set_links(span)

        model_provider = span.get_tag(PROVIDER)
        self._llmobs_set_metadata(span, model_provider)

        is_workflow = False

        llmobs_integration = "custom"
        if model_provider:
            if model_provider.startswith(BEDROCK_PROVIDER_NAME):
                llmobs_integration = "bedrock"
            # only the llm interface for Vertex AI will get instrumented
            elif model_provider.startswith(VERTEXAI_PROVIDER_NAME) and operation == "llm":
                llmobs_integration = "vertexai"
            # only the llm interface for Gemini will get instrumented
            elif model_provider.startswith(GEMINI_PROVIDER_NAME) and operation == "llm":
                llmobs_integration = "google_generativeai"
            elif model_provider.startswith(OPENAI_PROVIDER_NAME):
                llmobs_integration = "openai"
            elif operation == "chat" and model_provider.startswith(ANTHROPIC_PROVIDER_NAME):
                llmobs_integration = "anthropic"

            is_workflow = LLMObs._integration_is_enabled(llmobs_integration)

        if operation == "llm":
            self._llmobs_set_tags_from_llm(span, args, kwargs, response, is_workflow=is_workflow)
        elif operation == "chat":
            # langchain-openai will call a beta client "response_format" is passed in the kwargs, which we do not trace
            is_workflow = is_workflow and not (llmobs_integration == "openai" and ("response_format" in kwargs))
            self._llmobs_set_tags_from_chat_model(span, args, kwargs, response, is_workflow=is_workflow)
        elif operation == "chain":
            self._llmobs_set_meta_tags_from_chain(span, args, kwargs, outputs=response)
        elif operation == "embedding":
            self._llmobs_set_meta_tags_from_embedding(span, args, kwargs, response, is_workflow=is_workflow)
        elif operation == "retrieval":
            self._llmobs_set_meta_tags_from_similarity_search(span, args, kwargs, response, is_workflow=is_workflow)
        elif operation == "tool":
            self._llmobs_set_meta_tags_from_tool(span, tool_inputs=kwargs, tool_output=response)

    def _set_links(self, span: Span):
        """
        Sets span links for the given LangChain span, by doing the following:
        1. Determine the default invoker (parent llmobs span) and span linkage attributes (input-to-input)
        2. If the instance associated with the span is a step in a chain, look for the last traced step in the chain
            and establish an output-to-input relationship
        3. Set the span link on the span from the invoker
        4. Set the span link on the invoker from the span as an output-to-output relationship
            a. If the span is a step in the chain, we overwrite the existing output-to-output relationship,
                    until the last traced step is not overwritten
                i. This assumes only one output-to-output link for an individual chain
            b. If the span is not a step in the chain, we add the output-to-output relationship on top of
                the existing links, even if there is another output-to-output link
        """
        instance = self._instances.get(span)
        if not instance:
            return

        instance = _extract_bound(instance)
        parent_span = _get_nearest_llmobs_ancestor(span)

        prev_traced_step_idx = self._set_input_links(instance, span, parent_span)

        self._set_output_links(span, parent_span, prev_traced_step_idx)

    def _set_input_links(self, instance: Any, span: Span, parent_span: Union[Span, None]) -> int:
        """
        Sets input links (to: input) on the given span
        1. If the instance associated with the span is not a step in a chain, link from its parent span (input->input)
        2. If the instance associated with the span is a step in a chain, link from the last traced step in the chain
            a. This could be multiple steps, if the last step was a RunnableParallel
            b. If there was no previous traced step, link from the parent span (input->input)
            b. Otherwise, it would be an output->input relationship with the previously traced span(s)
        """
        if parent_span is None:
            return -1

        is_step = id(instance) in self._chain_steps

        if not is_step:
            self._set_span_links(span, [parent_span], "input", "input")

            return -1

        chain_instance = _extract_bound(self._instances.get(parent_span))
        steps = getattr(chain_instance, "steps", [])
        flatmap_chain_steps = _flattened_chain_steps(steps)
        prev_traced_step_idx = self._find_previous_traced_step_index(instance, flatmap_chain_steps)

        if prev_traced_step_idx == -1:
            self._set_span_links(span, [parent_span], "input", "input")

            return prev_traced_step_idx

        invoker_spans = []
        prev_traced_step = flatmap_chain_steps[prev_traced_step_idx]
        if isinstance(prev_traced_step, list):
            for parallel_step in prev_traced_step:
                if id(parallel_step) in self._spans:
                    invoker_spans.append(self._spans[id(parallel_step)])
        else:
            invoker_spans.append(self._spans[id(prev_traced_step)])

        self._set_span_links(span, invoker_spans, "output", "input")

        return prev_traced_step_idx

    def _find_previous_traced_step_index(self, instance, flatmap_chain_steps):
        """
        Finds the index in the list of steps of the last traced step in the chain before the current instance.
        """
        curr_idx = 0
        curr_step = flatmap_chain_steps[0]
        prev_traced_step_idx = -1

        while (
            curr_idx < len(flatmap_chain_steps)
            and id(curr_step) != id(instance)
            and not (isinstance(curr_step, list) and any(id(sub_step) == id(instance) for sub_step in curr_step))
        ):
            if id(curr_step) in self._spans or (
                isinstance(curr_step, list) and any(id(sub_step) in self._spans for sub_step in curr_step)
            ):
                prev_traced_step_idx = curr_idx
            curr_idx += 1
            curr_step = flatmap_chain_steps[curr_idx]

        return prev_traced_step_idx

    def _set_output_links(self, span: Span, parent_span: Union[Span, None], prev_traced_step_idx: int) -> None:
        """
        Sets the output links for the parent span of the given span (to: output)
        This is done by removing repeated span links from steps in a chain.
        We add output->output span links at every step.
        """
        if parent_span is None:
            return

        parent_links = parent_span._get_ctx_item(SPAN_LINKS) or []
        pop_indecies = self._get_popped_span_link_indecies(parent_span, parent_links, prev_traced_step_idx)

        self._set_span_links(parent_span, [span], "output", "output", popped_span_link_indecies=pop_indecies)

    def _get_popped_span_link_indecies(
        self, parent_span: Span, parent_links: List[Dict[str, Any]], prev_traced_step_idx: int
    ) -> List[int]:
        """
        Returns a list of indecies to pop from the parent span links list
        This is determined by if the parent span represents a chain, and if there are steps before the step
        represented by the span that need to be removed.

        This is a temporary stopgap until we trace virtually every step in the chain, and we know the last
        step will be the last one traced.
        """
        pop_indecies: List[int] = []
        parent_instance = self._instances.get(parent_span)
        if not parent_instance or prev_traced_step_idx == -1:
            return pop_indecies

        parent_instance = _extract_bound(parent_instance)
        if not hasattr(parent_instance, "steps"):  # chain instance
            return pop_indecies

        steps = getattr(parent_instance, "steps", [])
        flatmap_chain_steps = _flattened_chain_steps(steps)
        prev_traced_step = flatmap_chain_steps[prev_traced_step_idx]

        if isinstance(prev_traced_step, list):
            for parallel_step in prev_traced_step:
                if id(parallel_step) in self._spans:
                    invoker_span_id = self._spans[id(parallel_step)].span_id
                    link_idx = next(
                        (i for i, link in enumerate(parent_links) if link["span_id"] == str(invoker_span_id)), None
                    )
                    if link_idx is not None:
                        pop_indecies.append(link_idx)
        else:
            invoker_span_id = self._spans[id(prev_traced_step)].span_id
            link_idx = next((i for i, link in enumerate(parent_links) if link["span_id"] == str(invoker_span_id)), None)
            if link_idx is not None:
                pop_indecies.append(link_idx)

        return pop_indecies

    def _set_span_links(
        self,
        span: Span,
        from_spans: List[Span],
        link_from: str,
        link_to: str,
        popped_span_link_indecies: Optional[List[int]] = None,
    ) -> None:
        """Sets the span links on the given span along with the existing links."""
        existing_links = span._get_ctx_item(SPAN_LINKS) or []

        if popped_span_link_indecies:
            existing_links = [link for i, link in enumerate(existing_links) if i not in popped_span_link_indecies]

        links = [
            {
                "trace_id": "{:x}".format(from_span.trace_id),
                "span_id": str(from_span.span_id),
                "attributes": {"from": link_from, "to": link_to},
            }
            for from_span in from_spans
        ]
        span._set_ctx_item(SPAN_LINKS, existing_links + links)

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
            span._set_ctx_item(METADATA, metadata)

    def _llmobs_set_tags_from_llm(
        self, span: Span, args: List[Any], kwargs: Dict[str, Any], completions: Any, is_workflow: bool = False
    ) -> None:
        input_tag_key = INPUT_VALUE if is_workflow else INPUT_MESSAGES
        output_tag_key = OUTPUT_VALUE if is_workflow else OUTPUT_MESSAGES
        stream = span.get_tag("langchain.request.stream")

        prompts = get_argument_value(args, kwargs, 0, "input" if stream else "prompts")
        if isinstance(prompts, str) or not isinstance(prompts, list):
            prompts = [prompts]
        if stream:
            # chat and llm take the same input types for streamed calls
            input_messages = self._handle_stream_input_messages(prompts)
        else:
            input_messages = [{"content": str(prompt)} for prompt in prompts]

        span._set_ctx_items(
            {
                SPAN_KIND: "workflow" if is_workflow else "llm",
                MODEL_NAME: span.get_tag(MODEL) or "",
                MODEL_PROVIDER: span.get_tag(PROVIDER) or "",
                input_tag_key: input_messages,
            }
        )

        if span.error:
            span._set_ctx_item(output_tag_key, [{"content": ""}])
            return
        if stream:
            message_content = [{"content": completions}]  # single completion for streams
        else:
            message_content = [{"content": completion[0].text} for completion in completions.generations]
            if not is_workflow:
                input_tokens, output_tokens, total_tokens = self.check_token_usage_chat_or_llm_result(completions)
                if total_tokens > 0:
                    metrics = {
                        INPUT_TOKENS_METRIC_KEY: input_tokens,
                        OUTPUT_TOKENS_METRIC_KEY: output_tokens,
                        TOTAL_TOKENS_METRIC_KEY: total_tokens,
                    }
                    span._set_ctx_item(METRICS, metrics)
        span._set_ctx_item(output_tag_key, message_content)

    def _llmobs_set_tags_from_chat_model(
        self,
        span: Span,
        args: List[Any],
        kwargs: Dict[str, Any],
        chat_completions: Any,
        is_workflow: bool = False,
    ) -> None:
        span._set_ctx_items(
            {
                SPAN_KIND: "workflow" if is_workflow else "llm",
                MODEL_NAME: span.get_tag(MODEL) or "",
                MODEL_PROVIDER: span.get_tag(PROVIDER) or "",
            }
        )
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
                    role = getattr(message, "role", ROLE_MAPPING.get(getattr(message, "type", ""), ""))
                    input_messages.append({"content": str(content), "role": str(role)})
        span._set_ctx_item(input_tag_key, input_messages)

        if span.error:
            span._set_ctx_item(output_tag_key, [{"content": ""}])
            return

        output_messages = []
        if stream:
            content = chat_completions.content
            role = chat_completions.__class__.__name__.replace("MessageChunk", "").lower()  # AIMessageChunk --> ai
            span._set_ctx_item(output_tag_key, [{"content": content, "role": ROLE_MAPPING.get(role, "")}])
            return

        input_tokens, output_tokens, total_tokens = 0, 0, 0
        tokens_set_top_level = False
        if not is_workflow:
            # tokens are usually set at the top-level ChatResult or LLMResult object
            input_tokens, output_tokens, total_tokens = self.check_token_usage_chat_or_llm_result(chat_completions)
            tokens_set_top_level = total_tokens > 0

        tokens_per_choice_run_id: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for message_set in chat_completions.generations:
            for chat_completion in message_set:
                chat_completion_msg = chat_completion.message
                role = getattr(chat_completion_msg, "role", ROLE_MAPPING.get(chat_completion_msg.type, ""))
                output_message = {"content": str(chat_completion.text), "role": role}
                tool_calls_info = self._extract_tool_calls(chat_completion_msg)
                if tool_calls_info:
                    output_message["tool_calls"] = tool_calls_info
                output_messages.append(output_message)

                # if it wasn't set above, check for token usage on the AI message object level
                # these blocks contain the same count as what would be set at the top level.
                # do not append to the count, just set it once
                if not is_workflow and not tokens_set_top_level:
                    tokens, run_id = self.check_token_usage_ai_message(chat_completion_msg)
                    input_tokens, output_tokens, total_tokens = tokens
                    tokens_per_choice_run_id[run_id]["input_tokens"] = input_tokens
                    tokens_per_choice_run_id[run_id]["output_tokens"] = output_tokens
                    tokens_per_choice_run_id[run_id]["total_tokens"] = total_tokens

        if not is_workflow and not tokens_set_top_level:
            input_tokens = sum(v["input_tokens"] for v in tokens_per_choice_run_id.values())
            output_tokens = sum(v["output_tokens"] for v in tokens_per_choice_run_id.values())
            total_tokens = sum(v["total_tokens"] for v in tokens_per_choice_run_id.values())

        span._set_ctx_item(output_tag_key, output_messages)

        if not is_workflow and total_tokens > 0:
            metrics = {
                INPUT_TOKENS_METRIC_KEY: input_tokens,
                OUTPUT_TOKENS_METRIC_KEY: output_tokens,
                TOTAL_TOKENS_METRIC_KEY: total_tokens,
            }
            span._set_ctx_item(METRICS, metrics)

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
        if span.get_tag("langchain.request.stream"):
            inputs = get_argument_value(args, kwargs, 0, "input")
        else:
            inputs = kwargs
        formatted_inputs = ""
        if inputs is not None:
            formatted_inputs = format_langchain_io(inputs)
        formatted_outputs = ""
        if not span.error and outputs is not None:
            formatted_outputs = format_langchain_io(outputs)
        span._set_ctx_items({SPAN_KIND: "workflow", INPUT_VALUE: formatted_inputs, OUTPUT_VALUE: formatted_outputs})

    def _llmobs_set_meta_tags_from_embedding(
        self,
        span: Span,
        args: List[Any],
        kwargs: Dict[str, Any],
        output_embedding: Union[List[float], List[List[float]], None],
        is_workflow: bool = False,
    ) -> None:
        span._set_ctx_items(
            {
                SPAN_KIND: "workflow" if is_workflow else "embedding",
                MODEL_NAME: span.get_tag(MODEL) or "",
                MODEL_PROVIDER: span.get_tag(PROVIDER) or "",
            }
        )
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
                    formatted_inputs = format_langchain_io(input_texts)
                    span._set_ctx_item(input_tag_key, formatted_inputs)
                else:
                    if isinstance(input_texts, str):
                        input_texts = [input_texts]
                    input_documents = [Document(text=str(doc)) for doc in input_texts]
                    span._set_ctx_item(input_tag_key, input_documents)
        except TypeError:
            log.warning("Failed to serialize embedding input data to JSON")
        if span.error or output_embedding is None:
            span._set_ctx_item(output_tag_key, "")
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
            span._set_ctx_item(
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
        span._set_ctx_items(
            {
                SPAN_KIND: "workflow" if is_workflow else "retrieval",
                MODEL_NAME: span.get_tag(MODEL) or "",
                MODEL_PROVIDER: span.get_tag(PROVIDER) or "",
            }
        )
        input_query = get_argument_value(args, kwargs, 0, "query")
        if input_query is not None:
            formatted_inputs = format_langchain_io(input_query)
            span._set_ctx_item(INPUT_VALUE, formatted_inputs)
        if span.error or not output_documents or not isinstance(output_documents, list):
            span._set_ctx_item(OUTPUT_VALUE, "")
            return
        if is_workflow:
            span._set_ctx_item(OUTPUT_VALUE, "[{} document(s) retrieved]".format(len(output_documents)))
            return
        documents = []
        for d in output_documents:
            doc = Document(text=d.page_content)
            doc["id"] = getattr(d, "id", "")
            metadata = getattr(d, "metadata", {})
            doc["name"] = metadata.get("name", doc["id"])
            documents.append(doc)
        span._set_ctx_item(OUTPUT_DOCUMENTS, format_langchain_io(documents))
        # we set the value as well to ensure that the UI would display it in case the span was the root
        span._set_ctx_item(OUTPUT_VALUE, "[{} document(s) retrieved]".format(len(documents)))

    def _llmobs_set_meta_tags_from_tool(self, span: Span, tool_inputs: Dict[str, Any], tool_output: object) -> None:
        metadata = json.loads(str(span.get_tag(METADATA))) if span.get_tag(METADATA) else {}
        formatted_input = ""
        if tool_inputs is not None:
            tool_input = tool_inputs.get("input")
            # if tool_inputs.get("config"):
            #     metadata["tool_config"] = tool_inputs.get("config")
            if tool_inputs.get("info"):
                metadata["tool_info"] = tool_inputs.get("info")
            formatted_input = format_langchain_io(tool_input)
        formatted_outputs = ""
        if not span.error and tool_output is not None:
            formatted_outputs = format_langchain_io(tool_output)
        span._set_ctx_items(
            {
                SPAN_KIND: "tool",
                METADATA: metadata,
                INPUT_VALUE: formatted_input,
                OUTPUT_VALUE: formatted_outputs,
            }
        )

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

    def check_token_usage_chat_or_llm_result(self, result):
        """Checks for token usage on the top-level ChatResult or LLMResult object"""
        llm_output = getattr(result, "llm_output", {})
        if llm_output is None:  # in case it is explicitly set to None
            return 0, 0, 0

        token_usage = llm_output.get("token_usage", llm_output.get("usage_metadata", llm_output.get("usage", {})))
        if token_usage is None or not isinstance(token_usage, dict):  # in case it is explicitly set to None
            return 0, 0, 0

        # could either be "{prompt,completion}_tokens" or "{input,output}_tokens"
        input_tokens = token_usage.get("prompt_tokens", 0) or token_usage.get("input_tokens", 0) or 0
        output_tokens = token_usage.get("completion_tokens", 0) or token_usage.get("output_tokens", 0) or 0
        total_tokens = token_usage.get("total_tokens", input_tokens + output_tokens) or 0

        return input_tokens, output_tokens, total_tokens

    def check_token_usage_ai_message(self, ai_message):
        """Checks for token usage on an AI message object"""
        # depending on the provider + langchain-core version, the usage metadata can be in different places
        # either chat_completion_msg.usage_metadata or chat_completion_msg.response_metadata.{token}_usage
        usage = getattr(ai_message, "usage_metadata", None)

        run_id = getattr(ai_message, "id", None) or getattr(ai_message, "run_id", "")
        run_id_base = "-".join(run_id.split("-")[:-1]) if run_id else ""

        response_metadata = getattr(ai_message, "response_metadata", {}) or {}
        usage = usage or response_metadata.get("usage", {}) or response_metadata.get("token_usage", {})

        # could either be "{prompt,completion}_tokens" or "{input,output}_tokens"
        input_tokens = usage.get("input_tokens", 0) or usage.get("prompt_tokens", 0)
        output_tokens = usage.get("output_tokens", 0) or usage.get("completion_tokens", 0)
        total_tokens = usage.get("total_tokens", input_tokens + output_tokens)

        return (input_tokens, output_tokens, total_tokens), run_id_base
