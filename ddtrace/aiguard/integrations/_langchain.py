from collections.abc import AsyncIterator
from collections.abc import Iterator
from collections.abc import Sequence
import json
from typing import Any
from typing import Callable
from typing import Optional
import uuid

import wrapt

from ddtrace.aiguard import AIGuardAbortError
from ddtrace.aiguard import AIGuardClient
from ddtrace.aiguard import Function
from ddtrace.aiguard import Message
from ddtrace.aiguard import ToolCall
from ddtrace.aiguard._common import evaluate_auto
from ddtrace.aiguard._constants import AI_GUARD
from ddtrace.aiguard._context import Phase
from ddtrace.aiguard._context import aiguard_context
from ddtrace.aiguard._streaming import BufferedAIGuardAsyncStream
from ddtrace.aiguard._streaming import BufferedAIGuardStream
from ddtrace.aiguard._streaming import _is_traced_stream
from ddtrace.aiguard.messages import try_format_json
from ddtrace.contrib.internal.trace_utils import unwrap
from ddtrace.contrib.internal.trace_utils import wrap
import ddtrace.internal.logger as ddlogger
from ddtrace.internal.settings.aiguard import aiguard_config
from ddtrace.internal.utils import get_argument_value


logger = ddlogger.get_logger(__name__)


action_agents_classes = (
    "Agent",
    "xml.base.XMLAgent",
    "agent.RunnableAgent",
    "agent.RunnableMultiActionAgent",
    "agent.LLMSingleActionAgent",
    "openai_functions_agent.base.OpenAIFunctionsAgent",
    "openai_functions_multi_agent.base.OpenAIMultiFunctionsAgent",
)


def _langchain_patch(client: AIGuardClient) -> None:
    from functools import partial

    # langchain < 1.0: agents subclass one of the action-agent classes and
    # decide tool calls through ``plan`` / ``aplan``. Each is wrapped in its
    # own try/except because the available classes vary across versions.
    for class_ in action_agents_classes:
        try:
            wrap("langchain.agents", class_ + ".plan", partial(_langchain_agent_plan, client))
            wrap("langchain.agents", class_ + ".aplan", partial(_langchain_agent_aplan, client))
        except Exception:
            logger.debug("Failed to instrument %s", class_, exc_info=True)

    # langchain >= 1.0: agents are built with ``create_agent`` and client-side
    # tools are executed by langgraph's ``ToolNode``. Instrument the per-call
    # execution methods so AI Guard can block a tool call before it runs,
    # mirroring the legacy ``plan`` interception. ``langgraph`` is only present
    # on langchain >= 1.0, so guard the wrap with try/except.
    try:
        wrap("langgraph.prebuilt.tool_node", "ToolNode._run_one", partial(_langchain_toolnode_run_one, client))
        wrap("langgraph.prebuilt.tool_node", "ToolNode._arun_one", partial(_langchain_toolnode_arun_one, client))
    except Exception:
        logger.debug("Failed to instrument langgraph ToolNode", exc_info=True)

    # AI Guard owns the LangChain claims: each is taken and released in one frame of
    # these wrappers, so the handle never has to travel through the contrib.
    # Installed after the contrib's wrappers, so these are the outermost layer.
    try:
        from langchain_core.language_models.chat_models import BaseChatModel
        from langchain_core.language_models.llms import BaseLLM

        for base, is_chat in ((BaseChatModel, True), (BaseLLM, False)):
            wrap(base, "generate", _langchain_claimed_generate)
            wrap(base, "agenerate", _langchain_claimed_agenerate)
            wrap(base, "stream", partial(_langchain_stream, client, base, is_chat, False))
            wrap(base, "astream", partial(_langchain_stream, client, base, is_chat, True))
    except Exception:
        logger.debug("Failed to instrument langchain models", exc_info=True)


def _langchain_unpatch() -> None:
    try:
        import langchain.agents  # noqa: F401
    except ImportError:
        logger.debug("Failed to unpatch langchain.agents")
    else:
        for class_ in action_agents_classes:
            try:
                if "." in class_:
                    module_path, class_name = class_.rsplit(".", 1)
                    module = __import__("langchain.agents." + module_path, fromlist=[class_name])
                else:
                    module, class_name = langchain.agents, class_
                agent_class = getattr(module, class_name)
                unwrap(agent_class, "plan")
                unwrap(agent_class, "aplan")
            except Exception:
                logger.debug("Failed to unpatch %s", class_, exc_info=True)

    try:
        from langgraph.prebuilt.tool_node import ToolNode

        unwrap(ToolNode, "_run_one")
        unwrap(ToolNode, "_arun_one")
    except Exception:
        logger.debug("Failed to unpatch langgraph ToolNode", exc_info=True)

    try:
        from langchain_core.language_models.chat_models import BaseChatModel
        from langchain_core.language_models.llms import BaseLLM

        for base in (BaseChatModel, BaseLLM):
            for method in ("generate", "agenerate", "stream", "astream"):
                unwrap(base, method)
    except Exception:
        logger.debug("Failed to unpatch langchain models", exc_info=True)


def _langchain_claimed_generate(func: Callable[..., Any], instance: Any, args: Any, kwargs: Any) -> Any:
    """Hold both phases for a generate call: its .before and .after listeners evaluate both."""
    with aiguard_context(Phase.REQUEST, Phase.RESPONSE):
        return func(*args, **kwargs)


async def _langchain_claimed_agenerate(func: Callable[..., Any], instance: Any, args: Any, kwargs: Any) -> Any:
    """Async twin of _langchain_claimed_generate; the claim spans the awaited call in this task."""
    with aiguard_context(Phase.REQUEST, Phase.RESPONSE):
        return await func(*args, **kwargs)


def _langchain_stream(
    client: AIGuardClient,
    base: type,
    is_chat: bool,
    is_async: bool,
    func: Callable[..., Any],
    instance: Any,
    args: Any,
    kwargs: Any,
) -> Any:
    """Claim around each chunk pull and, with stream analysis on, evaluate before delivery.

    The .stream.before listener evaluates the request. The response is evaluated
    by buffering here, above the provider: LangChain wraps each provider chunk in
    its own read timeout, which a provider-level buffer draining the whole stream
    on the first read would trip. The buffer is skipped when the flag is off, or
    when LangChain serves stream() through invoke(), whose generate listeners
    evaluate the response already.
    """
    stream = func(*args, **kwargs)
    if not _is_traced_stream(stream):
        return stream
    claimed = _ClaimedAsyncStream(stream) if is_async else _ClaimedStream(stream)
    if not aiguard_config._ai_guard_analyze_stream_responses_enabled:
        return claimed
    if _stream_falls_back_to_invoke(instance, base, is_async, kwargs):
        return claimed

    chunks_seen: list[Any] = []

    def reconstruct(chunks: list[Any]) -> Any:
        chunks_seen[:] = chunks
        return _reconstruct_chat_stream(chunks) if is_chat else _reconstruct_llm_stream(chunks)

    def evaluate(response: Any) -> None:
        last_chunk = chunks_seen[-1] if chunks_seen else None
        _evaluate_streamed_response(client, instance, args, kwargs, is_chat, response, last_chunk)

    proxy = BufferedAIGuardAsyncStream if is_async else BufferedAIGuardStream
    return proxy(claimed, reconstruct=reconstruct, evaluate=evaluate)


class _ClaimedStream(wrapt.ObjectProxy):  # type: ignore[misc]  # wrapt ships no stubs
    """Hold both phases only while a chunk is being pulled from the LangChain stream.

    Claiming per pull, rather than from the first chunk to the last, keeps the
    caller's loop body unclaimed, so direct SDK calls made there are evaluated,
    and releases in the frame that claimed, so an early break or a close from
    another task cannot leave a claim behind.
    """

    def __iter__(self) -> Iterator[Any]:
        # Iterates the wrapped stream's own __iter__ so its cleanup on early exit is kept.
        iterator = iter(self.__wrapped__)
        try:
            while True:
                with aiguard_context(Phase.REQUEST, Phase.RESPONSE):
                    try:
                        chunk = next(iterator)
                    except StopIteration:
                        return
                yield chunk
        finally:
            close = getattr(iterator, "close", None)
            if close is not None:
                close()

    def __next__(self) -> Any:
        with aiguard_context(Phase.REQUEST, Phase.RESPONSE):
            return self.__wrapped__.__next__()

    def __enter__(self) -> Any:
        result = self.__wrapped__.__enter__()
        return self if result is self.__wrapped__ else _ClaimedStream(result)

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> Any:
        return self.__wrapped__.__exit__(exc_type, exc_val, exc_tb)


class _ClaimedAsyncStream(wrapt.ObjectProxy):  # type: ignore[misc]  # wrapt ships no stubs
    """Async twin of _ClaimedStream; the claim spans each awaited pull in this task."""

    async def __aiter__(self) -> AsyncIterator[Any]:
        # Iterates the wrapped stream's own __aiter__ so its cleanup on early exit is kept.
        iterator = self.__wrapped__.__aiter__()
        try:
            while True:
                with aiguard_context(Phase.REQUEST, Phase.RESPONSE):
                    try:
                        chunk = await iterator.__anext__()
                    except StopAsyncIteration:
                        return
                yield chunk
        finally:
            aclose = getattr(iterator, "aclose", None)
            if aclose is not None:
                await aclose()

    async def __anext__(self) -> Any:
        with aiguard_context(Phase.REQUEST, Phase.RESPONSE):
            return await self.__wrapped__.__anext__()

    async def __aenter__(self) -> Any:
        result = await self.__wrapped__.__aenter__()
        return self if result is self.__wrapped__ else _ClaimedAsyncStream(result)

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> Any:
        return await self.__wrapped__.__aexit__(exc_type, exc_val, exc_tb)


def _stream_falls_back_to_invoke(instance: Any, base: type, is_async: bool, kwargs: dict[str, Any]) -> bool:
    """Mirror LangChain's own decision to serve stream() through invoke().

    Newer langchain-core decides through _should_stream (disable_streaming,
    streaming=False, ...); older releases fall back only when the model overrides
    neither _stream nor, for astream, _astream.
    """
    should_stream = getattr(instance, "_should_stream", None)
    if callable(should_stream):
        extra = {k: v for k, v in kwargs.items() if k not in ("input", "config", "stop")}
        try:
            return not should_stream(async_api=is_async, **{**extra, "stream": True})
        except Exception:
            logger.debug("AI Guard langchain: _should_stream failed; buffering the stream", exc_info=True)
            return False
    cls = type(instance)
    if is_async and getattr(cls, "_astream", None) is not getattr(base, "_astream", None):
        return False
    return getattr(cls, "_stream", None) is getattr(base, "_stream", None)


def _reconstruct_chat_stream(chunks: list[Any]) -> Any:
    """Aggregate the buffered message chunks into the message the caller would assemble."""
    from functools import reduce
    import operator

    return reduce(operator.add, chunks) if chunks else None


def _reconstruct_llm_stream(chunks: list[Any]) -> str:
    return "".join(chunk for chunk in chunks if isinstance(chunk, str))


def _evaluate_streamed_response(
    client: AIGuardClient, instance: Any, args: Any, kwargs: Any, is_chat: bool, response: Any, last_chunk: Any
) -> None:
    """Evaluate a buffered LangChain stream's request plus its reconstructed response.

    On a clean verdict the tool calls are recorded as evaluated on the stream's
    last chunk: the caller (or the legacy agent's output parser) sums the replayed
    chunks, and chunk addition carries response_metadata into the aggregate, so the
    agent hook sees the record and does not evaluate the same call again.
    """
    from langchain_core.messages import HumanMessage

    if response is None:
        return
    # Conversion fails open: an error here must not break the caller's stream. Only
    # the verdict below may raise, and only AIGuardAbortError.
    try:
        input_arg = get_argument_value(args, kwargs, 0, "input")
        prompt_value = instance._convert_input(input_arg)
        if is_chat:
            request_messages = _convert_messages(prompt_value.to_messages())
            response_messages = _convert_response_message(response)
        else:
            request_messages = _convert_messages([HumanMessage(content=prompt_value.to_string())])
            response_messages = [Message(role="assistant", content=response)] if response else []
    except Exception:
        logger.debug("AI Guard langchain: failed to convert streamed call; skipping evaluation", exc_info=True)
        return
    if not response_messages:
        return
    if _evaluate_langchain_response(client, request_messages, response_messages) and is_chat:
        fingerprints = _message_tool_call_fingerprints(response)
        if fingerprints and last_chunk is not None:
            _record_evaluated_fingerprints(last_chunk, fingerprints)


def _langchain_agent_plan(
    client: AIGuardClient, func: Callable[..., Any], instance: Any, args: Any, kwargs: Any
) -> Any:
    action = func(*args, **kwargs)
    return _handle_agent_action_result(client, action, args, kwargs)


async def _langchain_agent_aplan(
    client: AIGuardClient, func: Callable[..., Any], instance: Any, args: Any, kwargs: Any
) -> Any:
    action = await func(*args, **kwargs)
    return _handle_agent_action_result(client, action, args, kwargs)


def _langchain_toolnode_run_one(
    client: AIGuardClient, func: Callable[..., Any], instance: Any, args: Any, kwargs: Any
) -> Any:
    """``ToolNode._run_one`` wrapper for langchain >= 1.0 ``create_agent`` agents.

    Evaluates the tool call *before* the tool executes so a blocking decision
    raises ``AIGuardAbortError`` and prevents execution.
    """
    _evaluate_langchain_tool_call(client, args, kwargs)
    return func(*args, **kwargs)


async def _langchain_toolnode_arun_one(
    client: AIGuardClient, func: Callable[..., Any], instance: Any, args: Any, kwargs: Any
) -> Any:
    """Async variant of :func:`_langchain_toolnode_run_one`."""
    _evaluate_langchain_tool_call(client, args, kwargs)
    return await func(*args, **kwargs)


def _try_parse_json(value: dict[str, Any], attribute: str) -> Any:
    json_str = value.get(attribute, None)
    if json_str is None:
        return None
    try:
        return json.loads(json_str)
    except Exception:
        return {attribute: json_str}


def _get_message_text(msg: Any) -> str:
    if isinstance(msg.content, str):
        return msg.content

    blocks = [
        block
        for block in msg.content
        if isinstance(block, str) or (block.get("type") == "text" and isinstance(block.get("text"), str))
    ]
    return "".join(block if isinstance(block, str) else block["text"] for block in blocks)


def _convert_messages(messages: list[Any]) -> list[Message]:
    from langchain_core.messages import ChatMessage
    from langchain_core.messages import HumanMessage
    from langchain_core.messages import SystemMessage
    from langchain_core.messages.ai import AIMessage
    from langchain_core.messages.function import FunctionMessage
    from langchain_core.messages.tool import ToolMessage

    result: list[Message] = []
    for message in messages:
        try:
            if isinstance(message, HumanMessage):
                result.append(Message(role="user", content=_get_message_text(message)))
            elif isinstance(message, SystemMessage):
                result.append(Message(role="system", content=_get_message_text(message)))
            elif isinstance(message, ChatMessage):
                result.append(Message(role=message.role, content=_get_message_text(message)))
            elif isinstance(message, AIMessage):
                if len(message.tool_calls) > 0:
                    tool_calls = [
                        ToolCall(
                            id=call.get("id", ""),
                            function=Function(
                                name=call.get("name", ""), arguments=try_format_json(call.get("args", {}))
                            ),
                        )
                        for call in message.tool_calls
                    ]
                    result.append(Message(role="assistant", tool_calls=tool_calls))
                if "function_call" in message.additional_kwargs:
                    function_call = message.additional_kwargs["function_call"]
                    tool_call = ToolCall(
                        id="",
                        function=Function(name=function_call.get("name"), arguments=function_call.get("arguments")),
                    )
                    result.append(Message(role="assistant", tool_calls=[tool_call]))
                if message.content:
                    result.append(Message(role="assistant", content=_get_message_text(message)))
            elif isinstance(message, ToolMessage):
                result.append(
                    Message(role="tool", tool_call_id=message.tool_call_id, content=_get_message_text(message))
                )
            elif isinstance(message, FunctionMessage):
                result.append(Message(role="tool", tool_call_id="", content=_get_message_text(message)))
        except Exception:
            logger.debug("Failed to convert message", exc_info=True)

    return result


# Records which tool calls the response listener already evaluated, so the agent
# execution hooks below do not scan the same call again a moment later.
#
# The record is the evaluated payload, not a boolean: a graph step or a
# human-in-the-loop update can rewrite a tool's name or arguments after the
# response was evaluated, and a bare "already checked" flag would wave the
# rewritten call straight through. Only a call that still matches what was
# evaluated is skipped.
#
# It lives in the message's own response_metadata, which travels with the message
# into langgraph state and the legacy agent's message_log, survives the copies
# langchain makes, and -- unlike additional_kwargs -- is not serialized back into
# provider requests, so it cannot leak into the customer's LLM payload. A weakref
# registry keyed by object identity was the alternative, but langchain-core 0.1.x
# messages are pydantic v1 and cannot be weak-referenced, which would silently
# disable the dedup on the oldest supported version.
_EVALUATED_KEY = "_dd.ai_guard.evaluated_tool_calls"


def _canonical_arguments(value: Any) -> str:
    """Stable text for a call's arguments, given either as a mapping or a JSON string.

    The two sides of the comparison disagree on shape: a message carries legacy
    function_call arguments as a JSON string while the agent hook holds the parsed
    mapping, so both are normalised through the same sorted dump.
    """
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            return value
    else:
        parsed = value
    try:
        return str(json.dumps(parsed, sort_keys=True, default=str))
    except Exception:
        return str(parsed)


def _tool_call_fingerprint(name: Any, arguments: Any) -> str:
    """Identify a tool call by what it would actually do, not by its id.

    Ids are absent on the legacy function_call path and are preserved across an
    edit on the langgraph path, so neither side can key on them.
    """
    return "{}\x00{}".format(name or "", _canonical_arguments(arguments))


def _message_tool_call_fingerprints(message: Any) -> list[str]:
    """Fingerprint every tool call an assistant message asks for."""
    fingerprints = []
    for call in getattr(message, "tool_calls", None) or ():
        if isinstance(call, dict):
            fingerprints.append(_tool_call_fingerprint(call.get("name"), call.get("args", {})))
    function_call = (getattr(message, "additional_kwargs", None) or {}).get("function_call")
    if isinstance(function_call, dict):
        fingerprints.append(_tool_call_fingerprint(function_call.get("name"), function_call.get("arguments")))
    return fingerprints


def _mark_tool_calls_evaluated(message: Any) -> None:
    """Record the tool calls this message asked for as evaluated."""
    fingerprints = _message_tool_call_fingerprints(message)
    if fingerprints:
        _record_evaluated_fingerprints(message, fingerprints)


def _record_evaluated_fingerprints(message: Any, fingerprints: list[str]) -> None:
    """Store fingerprints on message, which may be a chunk the evaluated calls are later summed from."""
    metadata = getattr(message, "response_metadata", None)
    if isinstance(metadata, dict):
        metadata[_EVALUATED_KEY] = fingerprints


def _tool_call_already_evaluated(message: Any, name: Any, arguments: Any) -> bool:
    """Whether this exact call -- same tool, same arguments -- was already evaluated."""
    if message is None:
        return False
    metadata = getattr(message, "response_metadata", None)
    if not isinstance(metadata, dict):
        return False
    evaluated = metadata.get(_EVALUATED_KEY)
    if not isinstance(evaluated, list):
        return False
    return _tool_call_fingerprint(name, arguments) in evaluated


def _message_has_tool_calls(message: Any) -> bool:
    return bool(_message_tool_call_fingerprints(message))


def _convert_response_message(message: Any) -> list[Message]:
    """Convert a model's own reply, keeping its text and tool calls in one turn.

    AIGuardClient.evaluate reads the span's target and tool_name off the trailing
    message, so splitting a mixed reply into a tool_calls turn followed by a text
    turn would classify the tool call as a prompt and lose its name. The
    request-side converter keeps them separate for history, where the trailing
    message is not the one being classified.
    """
    from langchain_core.messages.ai import AIMessage

    if not isinstance(message, AIMessage):
        return _convert_messages([message])

    tool_calls: list[ToolCall] = [
        ToolCall(
            id=call.get("id", ""),
            function=Function(name=call.get("name", ""), arguments=try_format_json(call.get("args", {}))),
        )
        for call in message.tool_calls or ()
        if isinstance(call, dict)
    ]
    function_call = message.additional_kwargs.get("function_call")
    if isinstance(function_call, dict):
        tool_calls.append(
            ToolCall(
                id="",
                function=Function(
                    name=function_call.get("name") or "",
                    arguments=function_call.get("arguments") or "{}",
                ),
            )
        )

    text = _get_message_text(message) if message.content else ""
    if not text and not tool_calls:
        return []
    ai_msg = Message(role="assistant")
    if text:
        ai_msg["content"] = text
    if tool_calls:
        ai_msg["tool_calls"] = tool_calls
    return [ai_msg]


def _convert_generations(generations: Sequence[Any]) -> list[Message]:
    """Convert one prompt's generations into AI Guard assistant messages.

    Handles both ChatGeneration (carries a message) and the plain Generation a
    non-chat LLM returns (carries only text).

    Tool calls are included. An application that calls bind_tools(...).invoke(...)
    and runs the returned calls itself has no agent hook in the path, so this is
    the only place that sees them; dropping them let a tool-call-only response
    reach user code unevaluated. Double-scanning for instrumented agents is
    avoided by _mark_tool_calls_evaluated rather than by omitting the calls.
    """
    result: list[Message] = []
    for generation in generations:
        try:
            message = getattr(generation, "message", None)
            if message is not None:
                result.extend(_convert_response_message(message))
            else:
                text = getattr(generation, "text", "") or ""
                if isinstance(text, str) and text:
                    result.append(Message(role="assistant", content=text))
        except Exception:
            logger.debug("Failed to convert langchain generation", exc_info=True)
    return result


def _evaluate_langchain_response(
    client: AIGuardClient, request_messages: list[Message], response_messages: list[Message]
) -> bool:
    """Evaluate a completed model call, re-raising AIGuardAbortError on a block.

    The abort propagates out of the contrib's after dispatch and replaces the
    model's return value, so blocked output never reaches the caller.

    Returns whether the evaluation actually ran. A transport failure is swallowed
    to keep the model call working, but the caller must not then record the tool
    calls as evaluated -- that would make the agent execution hooks skip the only
    remaining check on a call nothing ever scanned.
    """
    try:
        evaluate_auto(client, request_messages + response_messages, AI_GUARD.INTEGRATION_LANGCHAIN)
        return True
    except AIGuardAbortError:
        raise
    except Exception:
        logger.debug("Failed to evaluate chat model response", exc_info=True)
        return False


def _mark_generations_evaluated(generations: Sequence[Any]) -> None:
    """Flag the tool-call-bearing messages in a just-evaluated response.

    Only called once the evaluation returned without blocking, so a blocked call
    never leaves a message marked as cleared.
    """
    for generation in generations:
        message = getattr(generation, "message", None)
        if message is not None and _message_has_tool_calls(message):
            _mark_tool_calls_evaluated(message)


def _langchain_chatmodel_generate_after(client: AIGuardClient, message_lists: Any, result: Any) -> None:
    """Listener for langchain.chatmodel.generate.after and its async twin.

    Provider integrations (OpenAI, Anthropic) skip their own response evaluation
    while LangChain holds the response phase, so without this listener the
    model response reached the caller unevaluated.

    generations[i] holds the candidates produced for message_lists[i]; zip pairs
    them and tolerates a provider returning fewer of either.
    """
    generations = getattr(result, "generations", None) or []
    for messages, prompt_generations in zip(message_lists, generations):
        response_messages = _convert_generations(prompt_generations)
        if response_messages and _evaluate_langchain_response(client, _convert_messages(messages), response_messages):
            _mark_generations_evaluated(prompt_generations)


def _langchain_llm_generate_after(client: AIGuardClient, prompts: Any, result: Any) -> None:
    """Listener for langchain.llm.generate.after and its async twin -- see the chatmodel variant."""
    from langchain_core.messages import HumanMessage

    generations = getattr(result, "generations", None) or []
    for prompt, prompt_generations in zip(prompts, generations):
        response_messages = _convert_generations(prompt_generations)
        if response_messages:
            _evaluate_langchain_response(client, _convert_messages([HumanMessage(content=prompt)]), response_messages)


def _handle_agent_action_result(client: AIGuardClient, result: Any, args: Any, kwargs: Any) -> Any:
    try:
        from langchain_core.agents import AgentAction
        from langchain_core.agents import AgentActionMessageLog
    except ImportError:
        from langchain.agents import AgentAction
        from langchain.agents import AgentActionMessageLog

    for action in result if isinstance(result, Sequence) else [result]:
        if isinstance(action, AgentAction) and action.tool:
            # An AgentActionMessageLog carries the AIMessage it was parsed from.
            # Skip only when the call about to run is still the one that was
            # evaluated: an action whose tool or input was rewritten after the
            # response was scanned has to be evaluated again.
            if any(
                _tool_call_already_evaluated(m, action.tool, action.tool_input)
                for m in getattr(action, "message_log", None) or ()
            ):
                logger.debug("AI Guard langchain: agent action already evaluated with the model response")
                continue
            try:
                chat_history = kwargs["chat_history"] if "chat_history" in kwargs else []
                messages = _convert_messages(chat_history)
                prompt = kwargs["input"] if "input" in kwargs else None
                if prompt:
                    # TODO we are assuming user prompt
                    messages.append(Message(role="user", content=prompt))
                intermediate_steps = get_argument_value(args, kwargs, 0, "intermediate_steps")
                if intermediate_steps:
                    for intermediate_step, output in intermediate_steps:
                        if isinstance(intermediate_step, AgentActionMessageLog):
                            tool_call_id = str(uuid.uuid4())
                            tool_call = ToolCall(
                                id=tool_call_id,
                                function=Function(
                                    name=intermediate_step.tool,
                                    arguments=try_format_json(intermediate_step.tool_input),
                                ),
                            )
                            messages.append(Message(role="assistant", tool_calls=[tool_call]))

                            tool_output = str(output) if output else ""
                            if tool_output:
                                messages.append(Message(role="tool", tool_call_id=tool_call_id, content=tool_output))
                messages.append(
                    Message(
                        role="assistant",
                        tool_calls=[
                            ToolCall(
                                id="",
                                function=Function(name=action.tool, arguments=try_format_json(action.tool_input)),
                            )
                        ],
                    )
                )
                evaluate_auto(client, messages, AI_GUARD.INTEGRATION_LANGCHAIN)
            except AIGuardAbortError:
                raise
            except Exception:
                logger.debug("Failed to evaluate tool call", exc_info=True)

    return result


def _get_tool_runtime_messages(tool_runtime: Any) -> tuple[list[Any], Optional[Any]]:
    """Split a langgraph ToolRuntime into (history, the message that issued the calls).

    The agent state's last message is the ``AIMessage`` carrying the tool
    call(s) about to execute; it is dropped from the history because the
    specific call under evaluation is re-appended by the caller as a
    single-tool-call assistant message (mirroring the legacy agent-action
    conversion). It is returned separately so the caller can tell whether the
    response listener already evaluated those calls.

    LangChain >= 1.0 only. ``ToolRuntime`` and the langgraph
    ``ToolNode`` execution path this serves do not exist on earlier
    langchain releases; the legacy (< 1.0) agent path uses
    :func:`_handle_agent_action_result` instead.
    """
    from langchain_core.messages import AIMessage

    state = getattr(tool_runtime, "state", None)
    if isinstance(state, dict):
        messages = state.get("messages") or []
    else:
        messages = getattr(state, "messages", None) or []

    if messages and isinstance(messages[-1], AIMessage) and getattr(messages[-1], "tool_calls", None):
        return list(messages[:-1]), messages[-1]
    return list(messages), None


def _evaluate_langchain_tool_call(client: AIGuardClient, args: Any, kwargs: Any) -> None:
    """Evaluate a single tool call decided by a langchain >= 1.0 agent.

    ``langgraph``'s ``ToolNode._run_one`` / ``_arun_one`` execute one tool call
    each (``call`` is the first positional argument, ``tool_runtime`` the
    third). Re-raises ``AIGuardAbortError`` on a block so the contrib's
    ``allow_raise`` dispatch propagates the abort out of the agent invocation.
    """
    try:
        call = get_argument_value(args, kwargs, 0, "call")
        if not isinstance(call, dict):
            return
        tool_name = call.get("name")
        if not tool_name:
            return
        tool_runtime = get_argument_value(args, kwargs, 2, "tool_runtime")
        history, source_message = _get_tool_runtime_messages(tool_runtime)
        if _tool_call_already_evaluated(source_message, tool_name, call.get("args", {})):
            # Same tool, same arguments as the response listener already scanned.
            # An edited call does not match and is evaluated here as usual.
            logger.debug("AI Guard langchain: tool call already evaluated with the model response")
            return
        messages = _convert_messages(history)
        messages.append(
            Message(
                role="assistant",
                tool_calls=[
                    ToolCall(
                        id=call.get("id", ""),
                        function=Function(name=tool_name, arguments=try_format_json(call.get("args", {}))),
                    )
                ],
            )
        )
        evaluate_auto(client, messages, AI_GUARD.INTEGRATION_LANGCHAIN)
    except AIGuardAbortError:
        raise
    except Exception:
        logger.debug("Failed to evaluate tool call", exc_info=True)


def _langchain_chatmodel_generate_before(client: AIGuardClient, message_lists: Any) -> Optional[Any]:
    for messages in message_lists:
        result = _evaluate_langchain_messages(client, messages)
        if result is not None:
            return result
    return None


def _langchain_llm_generate_before(client: AIGuardClient, prompts: Any) -> Optional[Any]:
    """langchain.llm.[a]generate.before listener -- see the chatmodel variant."""
    from langchain_core.messages import HumanMessage

    for prompt in prompts:
        result = _evaluate_langchain_messages(client, [HumanMessage(content=prompt)])
        if result is not None:
            return result
    return None


def _langchain_chatmodel_stream_before(client: AIGuardClient, instance: Any, args: Any, kwargs: Any) -> Optional[Any]:
    input_arg = get_argument_value(args, kwargs, 0, "input")
    messages = instance._convert_input(input_arg).to_messages()
    return _evaluate_langchain_messages(client, messages)


def _langchain_llm_stream_before(client: AIGuardClient, instance: Any, args: Any, kwargs: Any) -> Optional[Any]:
    from langchain_core.messages import HumanMessage

    input_arg = get_argument_value(args, kwargs, 0, "input")
    prompt = instance._convert_input(input_arg).to_string()
    return _evaluate_langchain_messages(client, [HumanMessage(content=prompt)])


def _evaluate_langchain_messages(client: AIGuardClient, messages: list[Any]) -> Optional[Any]:
    """Evaluate a model call and re-raise ``AIGuardAbortError`` on a block.

    Re-raises so the contrib's ``core.dispatch(..., allow_raise=True)``
    propagates the abort. The ``AIGuardClient`` already gates on
    ``ai_guard_config._ai_guard_block``, so a raised error always represents
    a blocking decision. Allow / skip paths return ``None``.

    A model call is evaluated only when its trailing message represents a new
    logical event worth gating at the "before model" step:

    * ``HumanMessage`` — a new user prompt.
    * ``ToolMessage`` — a tool result fed back to the model during an agent
      loop (e.g. ``create_agent`` turns after a tool runs). AI Guard evaluates
      tool results at the "next before model"; without this an agent's tool
      outputs would never reach AI Guard. ``AIGuardClient`` tags the resulting
      span ``target=tool`` by resolving the tool name from the matching tool
      call.

    Other trailing messages (e.g. the model's own prior assistant output) are
    not new events and are skipped to avoid duplicate evaluations. The legacy
    (langchain < 1.0) agent path uses ``FunctionMessage`` for tool results and
    evaluates tool calls via ``Agent.plan``, so it is intentionally unaffected.
    """
    from langchain_core.messages import HumanMessage
    from langchain_core.messages import ToolMessage

    if len(messages) > 0 and isinstance(messages[-1], (HumanMessage, ToolMessage)):
        try:
            evaluate_auto(client, _convert_messages(messages), AI_GUARD.INTEGRATION_LANGCHAIN)
        except AIGuardAbortError:
            raise
        except Exception:
            logger.debug("Failed to evaluate chat model prompt", exc_info=True)
    return None
