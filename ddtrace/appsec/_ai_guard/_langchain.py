import json
from typing import Any
from typing import Sequence
import uuid

from ddtrace.appsec._ai_guard._context import reset_aiguard_context_active_current
from ddtrace.appsec._ai_guard._context import set_aiguard_context_active
from ddtrace.appsec._ai_guard.messages import try_format_json
from ddtrace.appsec.ai_guard import AIGuardAbortError
from ddtrace.appsec.ai_guard import AIGuardClient
from ddtrace.appsec.ai_guard import Function
from ddtrace.appsec.ai_guard import Message
from ddtrace.appsec.ai_guard import Options
from ddtrace.appsec.ai_guard import ToolCall
from ddtrace.contrib.internal.trace_utils import unwrap
from ddtrace.contrib.internal.trace_utils import wrap
import ddtrace.internal.logger as ddlogger
from ddtrace.internal.settings.asm import ai_guard_config
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


def _langchain_patch(client: AIGuardClient):
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


def _langchain_unpatch():
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


def _langchain_agent_plan(client: AIGuardClient, func, instance, args, kwargs):
    action = func(*args, **kwargs)
    return _handle_agent_action_result(client, action, args, kwargs)


async def _langchain_agent_aplan(client: AIGuardClient, func, instance, args, kwargs):
    action = await func(*args, **kwargs)
    return _handle_agent_action_result(client, action, args, kwargs)


def _langchain_toolnode_run_one(client: AIGuardClient, func, instance, args, kwargs):
    """``ToolNode._run_one`` wrapper for langchain >= 1.0 ``create_agent`` agents.

    Evaluates the tool call *before* the tool executes so a blocking decision
    raises ``AIGuardAbortError`` and prevents execution.
    """
    _evaluate_langchain_tool_call(client, args, kwargs)
    return func(*args, **kwargs)


async def _langchain_toolnode_arun_one(client: AIGuardClient, func, instance, args, kwargs):
    """Async variant of :func:`_langchain_toolnode_run_one`."""
    _evaluate_langchain_tool_call(client, args, kwargs)
    return await func(*args, **kwargs)


def _try_parse_json(value: dict, attribute: str) -> Any:
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


def _handle_agent_action_result(client: AIGuardClient, result, args, kwargs):
    try:
        from langchain_core.agents import AgentAction
        from langchain_core.agents import AgentActionMessageLog
    except ImportError:
        from langchain.agents import AgentAction
        from langchain.agents import AgentActionMessageLog

    for action in result if isinstance(result, Sequence) else [result]:
        if isinstance(action, AgentAction) and action.tool:
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
                client.evaluate(messages, Options(block=ai_guard_config._ai_guard_block))
            except AIGuardAbortError:
                raise
            except Exception:
                logger.debug("Failed to evaluate tool call", exc_info=True)

    return result


def _get_tool_runtime_messages(tool_runtime) -> list:
    """Extract the conversation history from a langgraph ``ToolRuntime``.

    The agent state's last message is the ``AIMessage`` carrying the tool
    call(s) about to execute; it is dropped here because the specific call
    under evaluation is re-appended by the caller as a single-tool-call
    assistant message (mirroring the legacy agent-action conversion).

    AIDEV-NOTE: langchain >= 1.0 only. ``ToolRuntime`` and the langgraph
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
        return list(messages[:-1])
    return list(messages)


def _evaluate_langchain_tool_call(client: AIGuardClient, args, kwargs):
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
        messages = _convert_messages(_get_tool_runtime_messages(tool_runtime))
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
        client.evaluate(messages, Options(block=ai_guard_config._ai_guard_block))
    except AIGuardAbortError:
        raise
    except Exception:
        logger.debug("Failed to evaluate tool call", exc_info=True)


def _langchain_chatmodel_generate_before(client: AIGuardClient, message_lists):
    set_aiguard_context_active()
    for messages in message_lists:
        result = _evaluate_langchain_messages(client, messages)
        if result is not None:
            return result
    return None


def _langchain_llm_generate_before(client: AIGuardClient, prompts):
    """``langchain.llm.[a]generate.before`` listener — see chatmodel variant."""
    from langchain_core.messages import HumanMessage

    set_aiguard_context_active()
    for prompt in prompts:
        result = _evaluate_langchain_messages(client, [HumanMessage(content=prompt)])
        if result is not None:
            return result
    return None


def _langchain_generate_finally(*args, **kwargs):
    """Paired ``.finally`` listener for the four langchain ``generate`` events.

    Releases the AI Guard active counter that the matching ``.before``
    listener bumped. Dispatched from the contrib's ``finally`` block so it
    fires on every exit path — success, block (``core.dispatch(..., allow_raise=True)``
    raises out of ``.before``), or exception inside the underlying LLM call. The
    counter reset is a no-op when the counter is already zero, so listener
    invocations that don't pair with a ``.before`` set are safe.
    """
    reset_aiguard_context_active_current()


def _langchain_chatmodel_stream_before(client: AIGuardClient, instance, args, kwargs):
    input_arg = get_argument_value(args, kwargs, 0, "input")
    messages = instance._convert_input(input_arg).to_messages()
    return _evaluate_langchain_messages(client, messages)


def _langchain_llm_stream_before(client: AIGuardClient, instance, args, kwargs):
    from langchain_core.messages import HumanMessage

    input_arg = get_argument_value(args, kwargs, 0, "input")
    prompt = instance._convert_input(input_arg).to_string()
    return _evaluate_langchain_messages(client, [HumanMessage(content=prompt)])


def _langchain_stream_started(*args, **kwargs):
    """Paired ``.stream.started`` listener for langchain stream events.

    Acquires the AI Guard active-context counter for the duration of stream
    iteration. Dispatched from ``BaseLangchainStreamHandler.start_stream``
    (in ``ddtrace/contrib/internal/langchain/utils.py``), which is called
    by ``TracedStream.__iter__`` / ``TracedAsyncStream.__aiter__`` on
    iteration entry — so a stream created but never iterated cannot bump
    the depth. The matching reset happens in
    :func:`_langchain_generate_finally` via the ``.stream.finally`` event.
    """
    set_aiguard_context_active()


def _evaluate_langchain_messages(client: AIGuardClient, messages):
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
            client.evaluate(_convert_messages(messages), Options(block=ai_guard_config._ai_guard_block))
        except AIGuardAbortError:
            raise
        except Exception:
            logger.debug("Failed to evaluate chat model prompt", exc_info=True)
    return None
