import re
import sys
from typing import Any
from typing import AsyncGenerator
from typing import Dict
from typing import Generator
from typing import List

from ddtrace.llmobs._integrations.utils import (
    openai_construct_completion_from_streamed_chunks,
    openai_construct_message_from_streamed_chunks,
)
import wrapt

from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._utils import _get_attr


try:
    from tiktoken import encoding_for_model

    tiktoken_available = True
except ModuleNotFoundError:
    tiktoken_available = False


log = get_logger(__name__)

_punc_regex = re.compile(r"[\w']+|[.,!?;~@#$%^&*()+/-]")


class BaseTracedOpenAIStream(wrapt.ObjectProxy):
    def __init__(self, wrapped, integration, span, kwargs, is_completion=False):
        super().__init__(wrapped)
        n = kwargs.get("n", 1) or 1
        prompts = kwargs.get("prompt", "")
        if is_completion and prompts and isinstance(prompts, list) and not isinstance(prompts[0], int):
            n *= len(prompts)
        self._dd_span = span
        self._streamed_chunks = [[] for _ in range(n)]
        self._dd_integration = integration
        self._is_completion = is_completion
        self._kwargs = kwargs


class TracedOpenAIStream(BaseTracedOpenAIStream):
    def __enter__(self):
        self.__wrapped__.__enter__()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.__wrapped__.__exit__(exc_type, exc_val, exc_tb)

    def __iter__(self):
        exception_raised = False
        try:
            for chunk in self.__wrapped__:
                self._extract_token_chunk(chunk)
                yield chunk
                _loop_handler(self._dd_span, chunk, self._streamed_chunks)
        except Exception:
            self._dd_span.set_exc_info(*sys.exc_info())
            exception_raised = True
            raise
        finally:
            if not exception_raised:
                _process_finished_stream(
                    self._dd_integration, self._dd_span, self._kwargs, self._streamed_chunks, self._is_completion
                )
            self._dd_span.finish()

    def __next__(self):
        try:
            chunk = self.__wrapped__.__next__()
            self._extract_token_chunk(chunk)
            _loop_handler(self._dd_span, chunk, self._streamed_chunks)
            return chunk
        except StopIteration:
            _process_finished_stream(
                self._dd_integration, self._dd_span, self._kwargs, self._streamed_chunks, self._is_completion
            )
            self._dd_span.finish()
            raise
        except Exception:
            self._dd_span.set_exc_info(*sys.exc_info())
            self._dd_span.finish()
            raise

    def _extract_token_chunk(self, chunk):
        """Attempt to extract the token chunk (last chunk in the stream) from the streamed response."""
        if not self._dd_span._get_ctx_item("_dd.auto_extract_token_chunk"):
            return
        choices = getattr(chunk, "choices")
        if not choices:
            return
        choice = choices[0]
        if not getattr(choice, "finish_reason", None):
            # Only the second-last chunk in the stream with token usage enabled will have finish_reason set
            return
        try:
            # User isn't expecting last token chunk to be present since it's not part of the default streamed response,
            # so we consume it and extract the token usage metadata before it reaches the user.
            usage_chunk = self.__wrapped__.__next__()
            self._streamed_chunks[0].insert(0, usage_chunk)
        except (StopIteration, GeneratorExit):
            return


class TracedOpenAIAsyncStream(BaseTracedOpenAIStream):
    async def __aenter__(self):
        await self.__wrapped__.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.__wrapped__.__aexit__(exc_type, exc_val, exc_tb)

    async def __aiter__(self):
        exception_raised = False
        try:
            async for chunk in self.__wrapped__:
                await self._extract_token_chunk(chunk)
                yield chunk
                _loop_handler(self._dd_span, chunk, self._streamed_chunks)
        except Exception:
            self._dd_span.set_exc_info(*sys.exc_info())
            exception_raised = True
            raise
        finally:
            if not exception_raised:
                _process_finished_stream(
                    self._dd_integration, self._dd_span, self._kwargs, self._streamed_chunks, self._is_completion
                )
            self._dd_span.finish()

    async def __anext__(self):
        try:
            chunk = await self.__wrapped__.__anext__()
            await self._extract_token_chunk(chunk)
            _loop_handler(self._dd_span, chunk, self._streamed_chunks)
            return chunk
        except StopAsyncIteration:
            _process_finished_stream(
                self._dd_integration, self._dd_span, self._kwargs, self._streamed_chunks, self._is_completion
            )
            self._dd_span.finish()
            raise
        except Exception:
            self._dd_span.set_exc_info(*sys.exc_info())
            self._dd_span.finish()
            raise

    async def _extract_token_chunk(self, chunk):
        """Attempt to extract the token chunk (last chunk in the stream) from the streamed response."""
        if not self._dd_span._get_ctx_item("_dd.auto_extract_token_chunk"):
            return
        choices = getattr(chunk, "choices")
        if not choices:
            return
        choice = choices[0]
        if not getattr(choice, "finish_reason", None):
            return
        try:
            usage_chunk = await self.__wrapped__.__anext__()
            self._streamed_chunks[0].insert(0, usage_chunk)
        except (StopAsyncIteration, GeneratorExit):
            return


def _compute_token_count(content, model):
    # type: (Union[str, List[int]], Optional[str]) -> Tuple[bool, int]
    """
    Takes in prompt/response(s) and model pair, and returns a tuple of whether or not the number of prompt
    tokens was estimated, and the estimated/calculated prompt token count.
    """
    num_prompt_tokens = 0
    estimated = False
    if model is not None and tiktoken_available is True:
        try:
            enc = encoding_for_model(model)
            if isinstance(content, str):
                num_prompt_tokens += len(enc.encode(content))
            elif content and isinstance(content, list) and isinstance(content[0], int):
                num_prompt_tokens += len(content)
            return estimated, num_prompt_tokens
        except KeyError:
            # tiktoken.encoding_for_model() will raise a KeyError if it doesn't have a tokenizer for the model
            estimated = True
    else:
        estimated = True

    # If model is unavailable or tiktoken is not imported, then provide a very rough estimate of the number of tokens
    return estimated, _est_tokens(content)


def _est_tokens(prompt):
    # type: (Union[str, List[int]]) -> int
    """
    Provide a very rough estimate of the number of tokens in a string prompt.
    Note that if the prompt is passed in as a token array (list of ints), the token count
    is just the length of the token array.
    """
    # If model is unavailable or tiktoken is not imported, then provide a very rough estimate of the number of tokens
    # Approximate using the following assumptions:
    #    * English text
    #    * 1 token ~= 4 chars
    #    * 1 token ~= ¾ words
    if not prompt:
        return 0
    est_tokens = 0
    if isinstance(prompt, str):
        est1 = len(prompt) / 4
        est2 = len(_punc_regex.findall(prompt)) * 0.75
        return round((1.5 * est1 + 0.5 * est2) / 2)
    elif isinstance(prompt, list) and isinstance(prompt[0], int):
        return len(prompt)
    return est_tokens


def _format_openai_api_key(openai_api_key):
    # type: (Optional[str]) -> Optional[str]
    """
    Returns `sk-...XXXX`, where XXXX is the last 4 characters of the provided OpenAI API key.
    This mimics how OpenAI UI formats the API key.
    """
    if not openai_api_key:
        return None
    return "sk-...%s" % openai_api_key[-4:]


def _is_generator(resp):
    # type: (...) -> bool
    import openai

    # In OpenAI v1, the response is type `openai.Stream` instead of Generator.
    if isinstance(resp, Generator):
        return True
    if hasattr(openai, "Stream") and isinstance(resp, openai.Stream):
        return True
    return False


def _is_async_generator(resp):
    # type: (...) -> bool
    import openai

    # In OpenAI v1, the response is type `openai.AsyncStream` instead of AsyncGenerator.
    if isinstance(resp, AsyncGenerator):
        return True
    if hasattr(openai, "AsyncStream") and isinstance(resp, openai.AsyncStream):
        return True
    return False


def _loop_handler(span, chunk, streamed_chunks):
    """Sets the openai model tag and appends the chunk to the correct index in the streamed_chunks list.

    When handling a streamed chat/completion response, this function is called for each chunk in the streamed response.
    """
    if span.get_tag("openai.response.model") is None:
        span.set_tag("openai.response.model", chunk.model)
    for choice in chunk.choices:
        streamed_chunks[choice.index].append(choice)
    if getattr(chunk, "usage", None):
        streamed_chunks[0].insert(0, chunk)


def _process_finished_stream(integration, span, kwargs, streamed_chunks, is_completion=False):
    prompts = kwargs.get("prompt", None)
    request_messages = kwargs.get("messages", None)
    try:
        if is_completion:
            formatted_completions = [
                openai_construct_completion_from_streamed_chunks(choice) for choice in streamed_chunks
            ]
        else:
            formatted_completions = [
                openai_construct_message_from_streamed_chunks(choice) for choice in streamed_chunks
            ]
        if integration.is_pc_sampled_span(span):
            _tag_streamed_response(integration, span, formatted_completions)
        _set_token_metrics(span, formatted_completions, prompts, request_messages, kwargs)
        operation = "completion" if is_completion else "chat"
        integration.llmobs_set_tags(span, args=[], kwargs=kwargs, response=formatted_completions, operation=operation)
    except Exception:
        log.warning("Error processing streamed completion/chat response.", exc_info=True)


def _tag_streamed_response(integration, span, completions_or_messages=None):
    """Tagging logic for streamed completions and chat completions."""
    for idx, choice in enumerate(completions_or_messages):
        text = choice.get("text", "")
        if text:
            span.set_tag_str("openai.response.choices.%d.text" % idx, integration.trunc(str(text)))
        message_role = choice.get("role", "")
        if message_role:
            span.set_tag_str("openai.response.choices.%d.message.role" % idx, str(message_role))
        message_content = choice.get("content", "")
        if message_content:
            span.set_tag_str(
                "openai.response.choices.%d.message.content" % idx, integration.trunc(str(message_content))
            )
        tool_calls = choice.get("tool_calls", [])
        if tool_calls:
            _tag_tool_calls(integration, span, tool_calls, idx)
        finish_reason = choice.get("finish_reason", "")
        if finish_reason:
            span.set_tag_str("openai.response.choices.%d.finish_reason" % idx, str(finish_reason))


def _set_token_metrics(span, response, prompts, messages, kwargs):
    """Set token span metrics on streamed chat/completion responses.
    If token usage is not available in the response, compute/estimate the token counts.
    """
    estimated = False
    if response and isinstance(response, list) and _get_attr(response[0], "usage", None):
        usage = response[0].get("usage", {})
        prompt_tokens = getattr(usage, "prompt_tokens", 0)
        completion_tokens = getattr(usage, "completion_tokens", 0)
        total_tokens = getattr(usage, "total_tokens", 0)
    else:
        model_name = span.get_tag("openai.response.model") or kwargs.get("model", "")
        estimated, prompt_tokens = _compute_prompt_tokens(model_name, prompts, messages)
        estimated, completion_tokens = _compute_completion_tokens(response, model_name)
        total_tokens = prompt_tokens + completion_tokens
    span.set_metric("openai.response.usage.prompt_tokens", prompt_tokens)
    span.set_metric("openai.request.prompt_tokens_estimated", int(estimated))
    span.set_metric("openai.response.usage.completion_tokens", completion_tokens)
    span.set_metric("openai.response.completion_tokens_estimated", int(estimated))
    span.set_metric("openai.response.usage.total_tokens", total_tokens)


def _compute_prompt_tokens(model_name, prompts=None, messages=None):
    """Compute token span metrics on streamed chat/completion requests.
    Only required if token usage is not provided in the streamed response.
    """
    num_prompt_tokens = 0
    estimated = False
    if messages:
        for m in messages:
            estimated, prompt_tokens = _compute_token_count(m.get("content", ""), model_name)
            num_prompt_tokens += prompt_tokens
    elif prompts:
        if isinstance(prompts, str) or isinstance(prompts, list) and isinstance(prompts[0], int):
            prompts = [prompts]
        for prompt in prompts:
            estimated, prompt_tokens = _compute_token_count(prompt, model_name)
            num_prompt_tokens += prompt_tokens
    return estimated, num_prompt_tokens


def _compute_completion_tokens(completions_or_messages, model_name):
    """Compute/Estimate the completion token count from the streamed response."""
    if not completions_or_messages:
        return False, 0
    estimated = False
    num_completion_tokens = 0
    for choice in completions_or_messages:
        content = choice.get("content", "") or choice.get("text", "")
        estimated, completion_tokens = _compute_token_count(content, model_name)
        num_completion_tokens += completion_tokens
    return estimated, num_completion_tokens


def _tag_tool_calls(integration, span, tool_calls, choice_idx):
    # type: (...) -> None
    """
    Tagging logic if function_call or tool_calls are provided in the chat response.
    Notes:
        - since function calls are deprecated and will be replaced with tool calls, apply the same tagging logic/schema.
        - streamed responses are processed and collected as dictionaries rather than objects,
          so we need to handle both ways of accessing values.
    """
    for idy, tool_call in enumerate(tool_calls):
        if hasattr(tool_call, "function"):
            # tool_call is further nested in a "function" object
            tool_call = tool_call.function
        function_arguments = _get_attr(tool_call, "arguments", "")
        function_name = _get_attr(tool_call, "name", "")
        span.set_tag_str(
            "openai.response.choices.%d.message.tool_calls.%d.arguments" % (choice_idx, idy),
            integration.trunc(str(function_arguments)),
        )
        span.set_tag_str(
            "openai.response.choices.%d.message.tool_calls.%d.name" % (choice_idx, idy), str(function_name)
        )
