from functools import singledispatch
from typing import Optional

from langchain_core.agents import AgentAction
from langchain_core.agents import AgentFinish
from langchain_core.messages.ai import AIMessage
from langchain_core.messages.base import BaseMessage
from langchain_core.messages.function import FunctionMessage
from langchain_core.messages.human import HumanMessage
from langchain_core.messages.system import SystemMessage
from langchain_core.messages.tool import ToolMessage

from ddtrace.appsec.ai_guard import AIGuardAbortError
from ddtrace.appsec.ai_guard import AIGuardClient
from ddtrace.appsec.ai_guard import Prompt
from ddtrace.appsec.ai_guard import ToolCall
from ddtrace.appsec.ai_guard._api_client import Evaluation
from ddtrace.contrib.internal.langchain.patch import DispatchResult
from ddtrace.contrib.internal.trace_utils import unwrap
from ddtrace.contrib.internal.trace_utils import wrap
import ddtrace.internal.logger as ddlogger


logger = ddlogger.get_logger(__name__)

client: AIGuardClient


def langchain_listen(core, ai_guard: AIGuardClient):
    global client
    client = ai_guard

    core.on("langchain.patch", _langchain_patch)
    core.on("langchain.unpatch", _langchain_unpatch)

    core.on("langchain.chatmodel.generate.before", _langchain_chatmodel_generate_before)
    core.on("langchain.chatmodel.agenerate.before", _langchain_chatmodel_generate_before)

    core.on("langchain.llm.generate.before", _langchain_llm_generate_before)
    core.on("langchain.llm.agenerate.before", _langchain_llm_generate_before)


@singledispatch
def to_evaluation(message: BaseMessage) -> Optional[Evaluation]:
    return None


@to_evaluation.register(HumanMessage)
def _(human: HumanMessage) -> Optional[Evaluation]:
    return Prompt(role="user", content=human.text())


@to_evaluation.register(SystemMessage)
def _(system: SystemMessage) -> Optional[Evaluation]:
    return Prompt(role="system", content=system.text())


@to_evaluation.register(AIMessage)
def _(ai: AIMessage) -> Optional[Evaluation]:
    return Prompt(role="assistant", content=ai.text())


@to_evaluation.register(ToolMessage)
def _(tool: ToolMessage) -> Optional[Evaluation]:
    return ToolCall(tool_name=tool.name, tool_args={}, output=tool.text())


@to_evaluation.register(FunctionMessage)
def _(fn: FunctionMessage) -> Optional[Evaluation]:
    return ToolCall(tool_name=fn.name, tool_args={}, output=fn.text())


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
    return _handle_agent_action_plan(action, kwargs)


async def _langchain_agent_aplan(func, instance, args, kwargs):
    action = await func(*args, **kwargs)
    return _handle_agent_action_plan(action, kwargs)


def _handle_agent_action_plan(action, kwargs):
    if isinstance(action, AgentAction) and "input" in kwargs:
        try:
            agent_input = kwargs["input"]
            if "chat_history" in kwargs:
                history = [e for e in (to_evaluation(msg) for msg in kwargs["chat_history"]) if e is not None]
            else:
                history = []
            # TODO dot not assume user prompt
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
            history = [e for e in (to_evaluation(msg) for msg in messages) if e is not None]
            prompt = history.pop(-1)
            try:
                if not client.evaluate_prompt(prompt["role"], prompt["content"], history=history):  # type: ignore[typeddict-item]
                    handler.proceed = False
                    break
            except AIGuardAbortError:
                handler.proceed = False
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
        except Exception:
            logger.debug("Failed to evaluate llm prompt", exc_info=True)

    if not handler.proceed:
        handler.result = AIGuardAbortError()
