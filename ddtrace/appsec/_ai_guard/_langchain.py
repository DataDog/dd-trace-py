import json
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Sequence

from ddtrace.appsec.ai_guard import AIGuardAbortError
from ddtrace.appsec.ai_guard import AIGuardClient
from ddtrace.appsec.ai_guard import Prompt
from ddtrace.appsec.ai_guard import ToolCall
from ddtrace.appsec.ai_guard._api_client import Evaluation
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
    return _handle_agent_action_result(client, action, kwargs)


async def _langchain_agent_aplan(client: AIGuardClient, func, instance, args, kwargs):
    action = await func(*args, **kwargs)
    return _handle_agent_action_result(client, action, kwargs)


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


def _convert_messages(messages: list[Any]) -> list[Evaluation]:
    from langchain_core.messages import ChatMessage
    from langchain_core.messages import HumanMessage
    from langchain_core.messages import SystemMessage
    from langchain_core.messages.ai import AIMessage
    from langchain_core.messages.function import FunctionMessage
    from langchain_core.messages.tool import ToolMessage

    result: List[Evaluation] = []
    tool_calls: Dict[str, ToolCall] = dict()
    function_call: Optional[ToolCall] = None
    for message in messages:
        try:
            if isinstance(message, HumanMessage):
                result.append(Prompt(role="user", content=_get_message_text(message)))
            elif isinstance(message, SystemMessage):
                result.append(Prompt(role="system", content=_get_message_text(message)))
            elif isinstance(message, ChatMessage):
                result.append(Prompt(role=message.role, content=_get_message_text(message)))
            elif isinstance(message, AIMessage):
                for call in message.tool_calls:
                    tool_call = ToolCall(tool_name=call["name"], tool_args=call["args"])
                    result.append(tool_call)
                    if call["id"]:
                        tool_calls[call["id"]] = tool_call
                if "function_call" in message.additional_kwargs:
                    call = message.additional_kwargs["function_call"]
                    function_call = ToolCall(tool_name=call.get("name"), tool_args=_try_parse_json(call, "arguments"))
                    result.append(function_call)
                if message.content:
                    result.append(Prompt(role="assistant", content=_get_message_text(message)))
            elif isinstance(message, ToolMessage):
                current_call = tool_calls.get(message.tool_call_id)
                if current_call:
                    current_call["output"] = _get_message_text(message)
            elif isinstance(message, FunctionMessage):
                if function_call and function_call["tool_name"] == message.name:
                    function_call["output"] = _get_message_text(message)
                    function_call = None
        except Exception:
            logger.debug("Failed to convert message", exc_info=True)

    return result


def _handle_agent_action_result(client: AIGuardClient, result, kwargs):
    try:
        from langchain_core.agents import AgentAction
        from langchain_core.agents import AgentFinish
    except ImportError:
        from langchain.agents import AgentAction
        from langchain.agents import AgentFinish

    for action in result if isinstance(result, Sequence) else [result]:
        if isinstance(action, AgentAction) and action.tool:
            try:
                history = _convert_messages(kwargs["chat_history"]) if "chat_history" in kwargs else []
                if "input" in kwargs:
                    # TODO we are assuming user prompt
                    history.append(Prompt(role="user", content=kwargs["input"]))
                tool_name = action.tool
                tool_input = action.tool_input
                if not client.evaluate_tool(tool_name, tool_input, history=history):
                    blocked_message = f"Tool call '{tool_name}' was blocked due to security policies."
                    return AgentFinish(return_values={"output": blocked_message}, log=blocked_message)
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
    for prompt in prompts:
        result = _evaluate_langchain_prompt(client, prompt)
        if result:
            return result
    return None


def _langchain_chatmodel_stream_before(client: AIGuardClient, instance, args, kwargs):
    input_arg = get_argument_value(args, kwargs, 0, "input")
    messages = instance._convert_input(input_arg).to_messages()
    return _evaluate_langchain_messages(client, messages)


def _langchain_llm_stream_before(client: AIGuardClient, instance, args, kwargs):
    input_arg = get_argument_value(args, kwargs, 0, "input")
    prompt = instance._convert_input(input_arg).to_string()
    return _evaluate_langchain_prompt(client, prompt)


def _evaluate_langchain_messages(client: AIGuardClient, messages):
    from langchain_core.messages import HumanMessage

    # only call evaluator when the last message is an actual user prompt
    if len(messages) > 0 and isinstance(messages[-1], HumanMessage):
        history = _convert_messages(messages)
        prompt = history.pop(-1)
        try:
            role, content = (prompt["role"], prompt["content"])  # type: ignore[typeddict-item]
            if not client.evaluate_prompt(role, content, history=history):
                return AIGuardAbortError()
        except AIGuardAbortError as e:
            return e
        except Exception:
            logger.debug("Failed to evaluate chat model prompt", exc_info=True)
    return None


def _evaluate_langchain_prompt(client: AIGuardClient, prompt):
    try:
        if not client.evaluate_prompt("user", prompt):
            return AIGuardAbortError()
    except AIGuardAbortError as e:
        return e
    except Exception:
        logger.debug("Failed to evaluate llm prompt", exc_info=True)
    return None
