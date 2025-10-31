import json
from typing import Any
from typing import List
from typing import Sequence
import uuid

from ddtrace.appsec.ai_guard import AIGuardAbortError
from ddtrace.appsec.ai_guard import AIGuardClient
from ddtrace.appsec.ai_guard import Function
from ddtrace.appsec.ai_guard import Message
from ddtrace.appsec.ai_guard import Options
from ddtrace.appsec.ai_guard import ToolCall
from ddtrace.contrib.internal.trace_utils import unwrap
from ddtrace.contrib.internal.trace_utils import wrap
import ddtrace.internal.logger as ddlogger
from ddtrace.internal.utils import get_argument_value


logger = ddlogger.get_logger(__name__)


action_agents_classes = (
    "agents.Agent",
    "agents.xml.base.XMLAgent",
    "agents.agent.RunnableAgent",
    "agents.agent.RunnableMultiActionAgent",
    "agents.agent.LLMSingleActionAgent",
    "agents.openai_functions_agent.base.OpenAIFunctionsAgent",
    "agents.openai_functions_multi_agent.base.OpenAIMultiFunctionsAgent",
)


def _langchain_patch(client: AIGuardClient):
    try:
        import langchain.agents  # noqa: F401
    except ImportError:
        logger.error("Failed to import langchain.agents, tool calls won't be instrumented")
        return

    from functools import partial

    for class_ in action_agents_classes:
        wrap("langchain", class_ + ".plan", partial(_langchain_agent_plan, client))
        wrap("langchain", class_ + ".aplan", partial(_langchain_agent_aplan, client))


def _langchain_unpatch():
    try:
        import langchain.agents  # noqa: F401
    except ImportError:
        logger.debug("Failed to unpatch langchain.agents")
        return

    for class_ in action_agents_classes:
        module_path, class_name = class_.rsplit(".", 1)
        module = __import__("langchain." + module_path, fromlist=[class_name])
        agent_class = getattr(module, class_name)
        unwrap(agent_class, "plan")
        unwrap(agent_class, "aplan")


def _langchain_agent_plan(client: AIGuardClient, func, instance, args, kwargs):
    action = func(*args, **kwargs)
    return _handle_agent_action_result(client, action, args, kwargs)


async def _langchain_agent_aplan(client: AIGuardClient, func, instance, args, kwargs):
    action = await func(*args, **kwargs)
    return _handle_agent_action_result(client, action, args, kwargs)


def _try_parse_json(value: dict, attribute: str) -> Any:
    json_str = value.get(attribute, None)
    if json_str is None:
        return None
    try:
        return json.loads(json_str)
    except Exception:
        return {attribute: json_str}


def _try_format_json(value: Any) -> str:
    if not value:
        return ""
    try:
        return json.dumps(value)
    except Exception:
        return str(value)


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

    result: List[Message] = []
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
                                name=call.get("name", ""), arguments=_try_format_json(call.get("args", {}))
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
                                    arguments=_try_format_json(intermediate_step.tool_input),
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
                                function=Function(name=action.tool, arguments=_try_format_json(action.tool_input)),
                            )
                        ],
                    )
                )
                client.evaluate(messages, Options(block=True))
            except AIGuardAbortError:
                raise
            except Exception:
                logger.debug("Failed to evaluate tool call", exc_info=True)

    return result


def _langchain_chatmodel_generate_before(client: AIGuardClient, message_lists):
    for messages in message_lists:
        result = _evaluate_langchain_messages(client, messages)
        if result:
            return result
    return None


def _langchain_llm_generate_before(client: AIGuardClient, prompts):
    from langchain_core.messages import HumanMessage

    for prompt in prompts:
        result = _evaluate_langchain_messages(client, [HumanMessage(content=prompt)])
        if result:
            return result
    return None


def _langchain_chatmodel_stream_before(client: AIGuardClient, instance, args, kwargs):
    input_arg = get_argument_value(args, kwargs, 0, "input")
    messages = instance._convert_input(input_arg).to_messages()
    return _evaluate_langchain_messages(client, messages)


def _langchain_llm_stream_before(client: AIGuardClient, instance, args, kwargs):
    from langchain_core.messages import HumanMessage

    input_arg = get_argument_value(args, kwargs, 0, "input")
    prompt = instance._convert_input(input_arg).to_string()
    return _evaluate_langchain_messages(client, [HumanMessage(content=prompt)])


def _evaluate_langchain_messages(client: AIGuardClient, messages):
    from langchain_core.messages import HumanMessage

    # only call evaluator when the last message is an actual user prompt
    if len(messages) > 0 and isinstance(messages[-1], HumanMessage):
        try:
            client.evaluate(_convert_messages(messages), Options(block=True))
        except AIGuardAbortError as e:
            return e
        except Exception:
            logger.debug("Failed to evaluate chat model prompt", exc_info=True)
    return None
