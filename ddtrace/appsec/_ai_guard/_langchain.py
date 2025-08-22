from functools import singledispatch
from typing import Optional

from langchain_core.messages.ai import AIMessage
from langchain_core.messages.base import BaseMessage
from langchain_core.messages.function import FunctionMessage
from langchain_core.messages.human import HumanMessage
from langchain_core.messages.system import SystemMessage
from langchain_core.messages.tool import ToolMessage

from ddtrace.appsec.ai_guard import AIGuardAbortError, ToolCall
from ddtrace.appsec.ai_guard import AIGuardClient
from ddtrace.appsec.ai_guard import Prompt
from ddtrace.appsec.ai_guard._api_client import Evaluation
from ddtrace.contrib.internal.langchain.patch import DispatchResult


def langchain_listen(core, ai_guard: AIGuardClient):
    chatmodel_generate_listener = _langchain_chatmodel_generate_before(ai_guard)
    llm_generate_listener = _langchain_llm_generate_before(ai_guard)
    tools_run_listener = _langchain_tools_run_before(ai_guard)
    core.on("langchain.chatmodel.generate.before", chatmodel_generate_listener)
    core.on("langchain.chatmodel.agenerate.before", chatmodel_generate_listener)
    core.on("langchain.llm.generate.before", llm_generate_listener)
    core.on("langchain.llm.agenerate.before", llm_generate_listener)
    core.on("langchain.tools.run.before", tools_run_listener)
    core.on("langchain.tools.arun.before", tools_run_listener)


@singledispatch
def to_evaluation(message: BaseMessage) -> Optional[Evaluation]:
    return None


@to_evaluation.register(HumanMessage)
def _(human: HumanMessage) -> Optional[Evaluation]:
    return Prompt(role="user", content=human.content) if human.content else None


@to_evaluation.register(SystemMessage)
def _(system: SystemMessage) -> Optional[Evaluation]:
    return Prompt(role="system", content=system.content) if system.content else None


@to_evaluation.register(AIMessage)
def _(ai: AIMessage) -> Optional[Evaluation]:
    return Prompt(role="assistant", content=ai.content) if ai.content else None


@to_evaluation.register(ToolMessage)
def _(tool: ToolMessage) -> Optional[Evaluation]:
    return ToolCall(tool_name=tool.name, tool_args={}, output=tool.content) if tool.content else None


@to_evaluation.register(FunctionMessage)
def _(fn: FunctionMessage) -> Optional[Evaluation]:
    return ToolCall(tool_name=fn.name, tool_args={}, output=fn.content) if fn.content else None


def _langchain_chatmodel_generate_before(ai_guard: AIGuardClient):
    def listener(message_lists, handler: DispatchResult):
        handler.proceed = True
        for messages in message_lists:
            history = [e for e in (to_evaluation(msg) for msg in messages) if e is not None]
            prompt: Optional[Prompt] = None
            # pop the last user prompt
            for i in range(len(history) - 1, -1, -1):
                if history[i].get("role", "") == "user":
                    prompt = history.pop(i)  # type: ignore[assignment]
                    break
            if prompt is not None:
                try:
                    if not ai_guard.evaluate_prompt(prompt["role"], prompt["content"], history=history):
                        handler.proceed = False
                        break
                except AIGuardAbortError:
                    handler.proceed = False

        if not handler.proceed:
            handler.result = AIGuardAbortError()

    return listener


def _langchain_llm_generate_before(ai_guard: AIGuardClient):
    def listener(prompts, handler: DispatchResult):
        handler.proceed = True
        for prompt in prompts:
            try:
                if not ai_guard.evaluate_prompt("user", prompt):
                    handler.proceed = False
                    break
            except AIGuardAbortError:
                handler.proceed = False

        if not handler.proceed:
            handler.result = AIGuardAbortError()

    return listener


def _langchain_tools_run_before(ai_guard: AIGuardClient):
    def listener(tool_input, tool_info, handler: DispatchResult):
        try:
            tool_name = tool_info["name"]
            if not ai_guard.evaluate_tool(tool_name, tool_input):
                handler.proceed = False
                handler.result = (
                    f"Tool call '{tool_name}' was blocked due to security policies, the tool was not executed."
                )
        except AIGuardAbortError as e:
            handler.proceed = False
            handler.result = e

    return listener
