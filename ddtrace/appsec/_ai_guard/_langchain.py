import json
from typing import Any

from langchain_core.agents import AgentAction
from langchain_core.agents import AgentFinish
from langchain_core.messages import BaseMessage
from langchain_core.messages import ChatMessage
from langchain_core.messages import HumanMessage
from langchain_core.messages import SystemMessage
from langchain_core.messages.ai import AIMessage
from langchain_core.messages.function import FunctionMessage
from langchain_core.messages.tool import ToolMessage

from ddtrace.appsec.ai_guard import AIGuardAbortError
from ddtrace.appsec.ai_guard import AIGuardClient
from ddtrace.appsec.ai_guard import Prompt
from ddtrace.appsec.ai_guard import ToolCall
from ddtrace.appsec.ai_guard import new_ai_guard_client
from ddtrace.appsec.ai_guard._api_client import Evaluation
from ddtrace.contrib.internal.langchain.patch import DispatchResult
from ddtrace.contrib.internal.trace_utils import unwrap
from ddtrace.contrib.internal.trace_utils import wrap
import ddtrace.internal.logger as ddlogger


logger = ddlogger.get_logger(__name__)
client: AIGuardClient = new_ai_guard_client()


action_agents_classes = (
    "agents.Agent",
    "agents.xml.base.XMLAgent",
    "agents.agent.RunnableAgent",
    "agents.agent.RunnableMultiActionAgent",
    "agents.agent.LLMSingleActionAgent",
    "agents.openai_functions_agent.base.OpenAIFunctionsAgent",
    "agents.openai_functions_multi_agent.base.OpenAIMultiFunctionsAgent",
)


def _langchain_patch():
    try:
        import langchain.agents  # noqa: F401
    except ImportError:
        logger.error("Failed to import langchain.agents, tool calls won't be instrumented")
        return

    for class_ in action_agents_classes:
        wrap("langchain", class_ + ".plan", _langchain_agent_plan)
        wrap("langchain", class_ + ".aplan", _langchain_agent_aplan)


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


def _langchain_agent_plan(func, instance, args, kwargs):
    action = func(*args, **kwargs)
    return _handle_agent_action_result(action, kwargs)


async def _langchain_agent_aplan(func, instance, args, kwargs):
    action = await func(*args, **kwargs)
    return _handle_agent_action_result(action, kwargs)


def _try_parse_json(value: str, default_name: str) -> Any:
    try:
        return json.loads(value)
    except Exception:
        return {default_name: value}


def _convert_messages(messages: list[BaseMessage]) -> list[Evaluation]:
    result = []
    tool_calls = dict()
    function_call = None
    for message in messages:
        if isinstance(message, HumanMessage):
            result.append(Prompt(role="user", content=message.text()))
        elif isinstance(message, SystemMessage):
            result.append(Prompt(role="system", content=message.text()))
        elif isinstance(message, ChatMessage):
            result.append(Prompt(role=message.role, content=message.text()))
        elif isinstance(message, AIMessage):
            for call in message.tool_calls:
                tool_call = ToolCall(tool_name=call["name"], tool_args=call["args"])
                result.append(tool_call)
                if call["id"]:
                    tool_calls[call["id"]] = tool_call
            if "function_call" in message.additional_kwargs:
                call = message.additional_kwargs["function_call"]
                function_call = ToolCall(
                    tool_name=call.get("name"), tool_args=_try_parse_json(call.get("arguments"), "arguments")
                )
                result.append(function_call)
            if message.content:
                result.append(Prompt(role=message.role, content=message.text()))
        elif isinstance(message, ToolMessage):
            tool_call = tool_calls.get(message.tool_call_id)
            if tool_call:
                tool_call["output"] = message.text()
        elif isinstance(message, FunctionMessage):
            if function_call and function_call["tool_name"] == message.name:
                function_call["output"] = message.text()
                function_call = None

    return result


def _handle_agent_action_result(action, kwargs):
    if isinstance(action, AgentAction) and "input" in kwargs:
        try:
            agent_input = kwargs["input"]
            history = _convert_messages(kwargs["chat_history"]) if "chat_history" in kwargs else []
            # TODO we are assuming user prompt
            history.append(Prompt(role="user", content=agent_input))
            tool_name = action.tool
            tool_input = action.tool_input
            if not client.evaluate_tool(tool_name, tool_input, history=history):
                blocked_message = f"Tool call '{tool_name}' was blocked due to security policies."
                return AgentFinish(return_values={"output": blocked_message}, log=blocked_message)
        except AIGuardAbortError:
            raise
        except Exception:
            logger.debug("Failed to evaluate tool call", exc_info=True)

    return action


def _langchain_chatmodel_generate_before(message_lists, handler: DispatchResult):
    handler.proceed = True
    for messages in message_lists:
        # only call evaluator when the last message is an actual user prompt
        if len(messages) > 0 and isinstance(messages[-1], HumanMessage):
            history = _convert_messages(messages)
            prompt = history.pop(-1)
            try:
                if not client.evaluate_prompt(prompt["role"], prompt["content"], history=history):  # type: ignore[typeddict-item]
                    handler.proceed = False
                    break
            except AIGuardAbortError:
                handler.proceed = False
                break
            except Exception:
                logger.debug("Failed to evaluate chat model prompt", exc_info=True)

    if not handler.proceed:
        handler.result = AIGuardAbortError()


def _langchain_llm_generate_before(prompts, handler: DispatchResult):
    handler.proceed = True
    for prompt in prompts:
        try:
            if not client.evaluate_prompt("user", prompt):
                handler.proceed = False
                break
        except AIGuardAbortError:
            handler.proceed = False
            break
        except Exception:
            logger.debug("Failed to evaluate llm prompt", exc_info=True)

    if not handler.proceed:
        handler.result = AIGuardAbortError()
