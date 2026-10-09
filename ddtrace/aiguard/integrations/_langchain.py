from collections.abc import Sequence
import contextvars
from functools import partial
import importlib
import inspect
import json
from typing import Any
from typing import Callable
from typing import Optional
import uuid

from ddtrace.aiguard import AIGuardAbortError
from ddtrace.aiguard import AIGuardClient
from ddtrace.aiguard import Function
from ddtrace.aiguard import Message
from ddtrace.aiguard import ToolCall
from ddtrace.aiguard._common import evaluate_auto
from ddtrace.aiguard._constants import AI_GUARD
from ddtrace.aiguard._context import reset_aiguard_context_active
from ddtrace.aiguard._context import set_aiguard_context_active
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

    # With stream analysis on, a LangChain stream is read in full and its response evaluated before the caller
    # gets a chunk, and the run manager's callbacks are held until then, inside generate too (see _CallScope).
    for module_name, method, make_wrapper in _MODEL_CALL_TARGETS:
        try:
            wrap(module_name, method, make_wrapper(client))
            class_name, method_name = method.split(".")
            owner = getattr(importlib.import_module(module_name), class_name)
            _installed_wrappers.append((owner, method_name, owner.__dict__[method_name]))
        except Exception:
            logger.debug("Failed to instrument %s.%s", module_name, method, exc_info=True)


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

    while _installed_wrappers:
        owner, method_name, installed = _installed_wrappers.pop()
        # Remove only AI Guard's own layer: a wrapper installed after it is left in place.
        if owner.__dict__.get(method_name) is installed:
            setattr(owner, method_name, installed.__wrapped__)
        else:
            logger.debug("AI Guard langchain: %s.%s was wrapped again; leaving it", owner, method_name)


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
    if not fingerprints:
        return
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
    while the LangChain context counter is active, so without this listener the
    model response reached the caller unevaluated.

    generations[i] holds the candidates produced for message_lists[i]; zip pairs
    them and tolerates a provider returning fewer of either.
    """
    if _evaluated_at_run_end():
        return
    generations = getattr(result, "generations", None) or []
    for messages, prompt_generations in zip(message_lists, generations):
        response_messages = _convert_generations(prompt_generations)
        if response_messages and _evaluate_langchain_response(client, _convert_messages(messages), response_messages):
            _mark_generations_evaluated(prompt_generations)


def _langchain_llm_generate_after(client: AIGuardClient, prompts: Any, result: Any) -> None:
    """Listener for langchain.llm.generate.after and its async twin -- see the chatmodel variant."""
    from langchain_core.messages import HumanMessage

    if _evaluated_at_run_end():
        return
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
    message_lists = list(message_lists)
    _SCOPES.set(_SCOPES.get() + (_CallScope(client, message_lists, set_aiguard_context_active()),))
    for messages in message_lists:
        result = _evaluate_langchain_messages(client, messages)
        if result is not None:
            return result
    return None


def _langchain_llm_generate_before(client: AIGuardClient, prompts: Any) -> Optional[Any]:
    """``langchain.llm.[a]generate.before`` listener — see chatmodel variant."""
    from langchain_core.messages import HumanMessage

    requests = [[HumanMessage(content=prompt)] for prompt in prompts]
    _SCOPES.set(_SCOPES.get() + (_CallScope(client, requests, set_aiguard_context_active()),))
    for messages in requests:
        result = _evaluate_langchain_messages(client, messages)
        if result is not None:
            return result
    return None


def _langchain_generate_finally(*args: Any, **kwargs: Any) -> None:
    """Release the claim of the matching .before and drop the callbacks it still holds.

    Dispatched from the contrib's finally block, so it runs on success, on a block and on an error. Only a
    generate scope (the one that holds a claim) is popped, so an unpaired call releases nothing else.
    """
    scopes = _SCOPES.get()
    if scopes and scopes[-1].claim is not None:
        _SCOPES.set(scopes[:-1])
        reset_aiguard_context_active(scopes[-1].claim)


def _langchain_chatmodel_stream_before(client: AIGuardClient, instance: Any, args: Any, kwargs: Any) -> Optional[Any]:
    input_arg = get_argument_value(args, kwargs, 0, "input")
    messages = instance._convert_input(input_arg).to_messages()
    return _evaluate_langchain_messages(client, messages)


def _langchain_llm_stream_before(client: AIGuardClient, instance: Any, args: Any, kwargs: Any) -> Optional[Any]:
    from langchain_core.messages import HumanMessage

    input_arg = get_argument_value(args, kwargs, 0, "input")
    prompt = instance._convert_input(input_arg).to_string()
    return _evaluate_langchain_messages(client, [HumanMessage(content=prompt)])


def _langchain_stream_started(state: dict[str, Any], *args: Any, **kwargs: Any) -> None:
    """Claim the AI Guard context when iteration of a LangChain stream starts; .stream.finally releases it.

    The handle is kept on the stream's own state, so a stream closed from another asyncio task releases it.
    """
    state["aiguard_claim"] = set_aiguard_context_active()


def _langchain_stream_finally(state: dict[str, Any], *args: Any, **kwargs: Any) -> None:
    reset_aiguard_context_active(state.pop("aiguard_claim", None))


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


class _CallScope:
    """One LangChain model call: the run-manager callbacks it holds until its response is evaluated.

    With stream analysis on, a model that streams reports each token to the run manager before the response
    is evaluated, which would reach callbacks (astream_events, LangGraph's messages mode) too early. The
    response of a single-prompt call is evaluated in on_llm_end, and the held callbacks are sent only after.
    A generate scope also holds the call's claim.
    """

    __slots__ = ("client", "requests", "claim", "holds", "held", "evaluated", "ends")

    def __init__(self, client: AIGuardClient, requests: list[list[Any]], claim: Any = None) -> None:
        self.client = client
        self.requests = requests
        self.claim = claim
        self.holds = aiguard_config._ai_guard_analyze_stream_responses_enabled
        # Held callbacks per run, by run_id: the sync manager LangChain derives with get_sync() shares it.
        self.held: dict[Any, list[Callable[[], Any]]] = {}
        self.evaluated = False
        # A buffered stream keeps the run's on_llm_end until its chunks are returned: astream_events() drops
        # the stream events of a run that already ended. None for a generate call, which ends at once.
        self.ends: Optional[list[Callable[[], Any]]] = None

    def evaluate_run_end(self, response: Any) -> None:
        """Evaluate a single-prompt call's response; AIGuardAbortError on a block.

        With several prompts runs cannot be paired with prompts here (cached prompts get no run), so
        .generate.after evaluates them.
        """
        if self.evaluated or len(self.requests) != 1:
            return
        self.evaluated = True
        generations = (getattr(response, "generations", None) or [[]])[0]
        response_messages = _convert_generations(generations)
        if response_messages and _evaluate_langchain_response(
            self.client, _convert_messages(self.requests[0]), response_messages
        ):
            _mark_generations_evaluated(generations)


# The LangChain model calls in progress in this context, innermost last.
_SCOPES: contextvars.ContextVar[tuple[_CallScope, ...]] = contextvars.ContextVar(
    "ai_guard_langchain_scopes", default=()
)


def _holding_scope() -> Optional[_CallScope]:
    scopes = _SCOPES.get()
    return scopes[-1] if scopes and scopes[-1].holds else None


def _evaluated_at_run_end() -> bool:
    scopes = _SCOPES.get()
    return bool(scopes) and scopes[-1].evaluated


def _stream_request(is_chat: bool, instance: Any, args: Any, kwargs: Any) -> list[list[Any]]:
    """The request of a stream() call, as .stream.before reads it; empty when it cannot be read."""
    from langchain_core.messages import HumanMessage

    try:
        prompt_value = instance._convert_input(get_argument_value(args, kwargs, 0, "input"))
        return [prompt_value.to_messages() if is_chat else [HumanMessage(content=prompt_value.to_string())]]
    except Exception:
        logger.debug("AI Guard langchain: failed to read the streamed request", exc_info=True)
        return []


def _langchain_buffered_stream(
    client: AIGuardClient, is_chat: bool, func: Callable[..., Any], instance: Any, args: Any, kwargs: Any
) -> Any:
    """stream() wrapper: with stream analysis on, read every chunk and evaluate the response before any is returned."""
    stream = func(*args, **kwargs)
    if not aiguard_config._ai_guard_analyze_stream_responses_enabled:
        return stream
    return _read_then_replay(_stream_scope(client, is_chat, instance, args, kwargs), stream)


def _stream_scope(client: AIGuardClient, is_chat: bool, instance: Any, args: Any, kwargs: Any) -> _CallScope:
    scope = _CallScope(client, _stream_request(is_chat, instance, args, kwargs))
    scope.ends = []
    return scope


def _read_then_replay(scope: _CallScope, stream: Any) -> Any:
    # The response is evaluated at the run's on_llm_end, which LangChain calls while the stream is read.
    _SCOPES.set(_SCOPES.get() + (scope,))
    try:
        chunks = list(stream)
    finally:
        _pop_scope(scope)
    try:
        yield from chunks
    finally:
        for end in scope.ends or ():
            end()


def _langchain_buffered_astream(
    client: AIGuardClient, is_chat: bool, func: Callable[..., Any], instance: Any, args: Any, kwargs: Any
) -> Any:
    """astream() wrapper: async twin of _langchain_buffered_stream."""
    stream = func(*args, **kwargs)
    if not aiguard_config._ai_guard_analyze_stream_responses_enabled:
        return stream
    return _aread_then_replay(_stream_scope(client, is_chat, instance, args, kwargs), stream)


async def _aread_then_replay(scope: _CallScope, stream: Any) -> Any:
    _SCOPES.set(_SCOPES.get() + (scope,))
    try:
        chunks = [chunk async for chunk in stream]
    finally:
        _pop_scope(scope)
    try:
        for chunk in chunks:
            yield chunk
    finally:
        for end in scope.ends or ():
            await _call(end)


def _pop_scope(scope: _CallScope) -> None:
    scopes = _SCOPES.get()
    if scopes and scopes[-1] is scope:
        _SCOPES.set(scopes[:-1])


def _run_key(run_manager: Any) -> Any:
    return getattr(run_manager, "run_id", None) or id(run_manager)


def _langchain_run_new_token(func: Callable[..., Any], instance: Any, args: Any, kwargs: Any) -> Any:
    scope = _holding_scope()
    if scope is None:
        return func(*args, **kwargs)
    scope.held.setdefault(_run_key(instance), []).append(partial(func, *args, **kwargs))
    return None


async def _langchain_arun_new_token(func: Callable[..., Any], instance: Any, args: Any, kwargs: Any) -> Any:
    scope = _holding_scope()
    if scope is None:
        return await func(*args, **kwargs)
    scope.held.setdefault(_run_key(instance), []).append(partial(func, *args, **kwargs))
    return None


def _langchain_run_end(func: Callable[..., Any], instance: Any, args: Any, kwargs: Any) -> Any:
    """Evaluate the response first; the held tokens and the end reach callbacks only if it is allowed."""
    scope = _holding_scope()
    if scope is not None:
        held = scope.held.pop(_run_key(instance), ())
        try:
            scope.evaluate_run_end(get_argument_value(args, kwargs, 0, "response"))
        except AIGuardAbortError as error:
            # LangChain does not report an error raised here; close the run so callbacks see it end.
            instance.on_llm_error(error)
            raise
        for call in held:
            call()
        if scope.ends is not None:
            scope.ends.append(partial(func, *args, **kwargs))
            return None
    return func(*args, **kwargs)


async def _langchain_arun_end(func: Callable[..., Any], instance: Any, args: Any, kwargs: Any) -> Any:
    scope = _holding_scope()
    if scope is not None:
        held = scope.held.pop(_run_key(instance), ())
        try:
            scope.evaluate_run_end(get_argument_value(args, kwargs, 0, "response"))
        except AIGuardAbortError as error:
            await instance.on_llm_error(error)
            raise
        for call in held:
            await _call(call)
        if scope.ends is not None:
            scope.ends.append(partial(func, *args, **kwargs))
            return None
    return await func(*args, **kwargs)


async def _call(call: Callable[[], Any]) -> None:
    # A callback held from the sync manager LangChain derives with get_sync() returns no awaitable.
    result = call()
    if inspect.isawaitable(result):
        await result


# The wrappers AI Guard installed on the model-call targets, so unpatch removes only its own layer.
_installed_wrappers: list[tuple[type, str, Any]] = []

_CHAT_MODELS = "langchain_core.language_models.chat_models"
_LLMS = "langchain_core.language_models.llms"
_RUN_MANAGERS = "langchain_core.callbacks.manager"

# The LangChain methods AI Guard wraps for stream buffering and callback holding, with each one's wrapper.
_MODEL_CALL_TARGETS: tuple[tuple[str, str, Callable[[AIGuardClient], Callable[..., Any]]], ...] = (
    (_CHAT_MODELS, "BaseChatModel.stream", lambda client: partial(_langchain_buffered_stream, client, True)),
    (_CHAT_MODELS, "BaseChatModel.astream", lambda client: partial(_langchain_buffered_astream, client, True)),
    (_LLMS, "BaseLLM.stream", lambda client: partial(_langchain_buffered_stream, client, False)),
    (_LLMS, "BaseLLM.astream", lambda client: partial(_langchain_buffered_astream, client, False)),
    (_RUN_MANAGERS, "CallbackManagerForLLMRun.on_llm_new_token", lambda client: _langchain_run_new_token),
    (_RUN_MANAGERS, "CallbackManagerForLLMRun.on_llm_end", lambda client: _langchain_run_end),
    (_RUN_MANAGERS, "AsyncCallbackManagerForLLMRun.on_llm_new_token", lambda client: _langchain_arun_new_token),
    (_RUN_MANAGERS, "AsyncCallbackManagerForLLMRun.on_llm_end", lambda client: _langchain_arun_end),
)
