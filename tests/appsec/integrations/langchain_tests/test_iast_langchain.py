import json
from typing import AsyncIterator
from typing import Iterator

from langchain.agents import AgentExecutor
from langchain.agents import AgentType
from langchain.agents import create_openai_functions_agent
from langchain.agents import initialize_agent
from langchain.agents.output_parsers.openai_functions import OpenAIFunctionsAgentOutputParser
from langchain_community.tools.shell.tool import ShellTool
from langchain_core.agents import AgentActionMessageLog
from langchain_core.language_models import BaseChatModel
from langchain_core.language_models import FakeListChatModel
from langchain_core.language_models.fake import FakeListLLM
from langchain_core.messages import AIMessage
from langchain_core.messages import AIMessageChunk
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatGeneration
from langchain_core.outputs import ChatGenerationChunk
from langchain_core.outputs import ChatResult
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.prompts import MessagesPlaceholder
from langchain_core.prompts import PromptTemplate
import pytest

from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
from ddtrace.appsec._iast._taint_tracking._taint_objects_base import is_pyobject_tainted
from ddtrace.appsec._iast.constants import VULN_CMDI
from tests.appsec.iast.conftest import iast_span_defaults  # noqa: F401
from tests.appsec.iast.taint_sinks.conftest import _get_span_report


TEST_FILE = "tests/appsec/integrations/langchain_tests/test_iast_langchain.py"


def test_prompt_template_format(iast_span_defaults):  # noqa: F811
    template = PromptTemplate.from_template("{prompt}")
    prompt = prepare_tainted_prompt()
    result = template.format(prompt=prompt)
    assert is_pyobject_tainted(result)


async def test_prompt_template_aformat(iast_span_defaults):  # noqa: F811
    template = PromptTemplate.from_template("{prompt}")
    prompt = prepare_tainted_prompt()
    result = await template.aformat(prompt=prompt)
    assert is_pyobject_tainted(result)


def test_chat_prompt_template_format(iast_span_defaults):  # noqa: F811
    template = ChatPromptTemplate.from_messages([("system", "You are a helpful assistant."), ("user", "{prompt}")])
    prompt = prepare_tainted_prompt()
    result = template.format(prompt=prompt)
    assert is_pyobject_tainted(result)


async def test_chat_prompt_template_aformat(iast_span_defaults):  # noqa: F811
    template = ChatPromptTemplate.from_messages([("system", "You are a helpful assistant."), ("user", "{prompt}")])
    prompt = prepare_tainted_prompt()
    result = await template.aformat(prompt=prompt)
    assert is_pyobject_tainted(result)


def test_openai_functions_agent_output_parser(iast_span_defaults):  # noqa: F811
    ai_message = AIMessage(
        content="Some content",
        additional_kwargs=dict(
            function_call={"name": "my-tool", "arguments": taint_string(json.dumps({"arg1": "value1"}))}
        ),
    )
    generation = ChatGeneration(message=ai_message)
    parser = OpenAIFunctionsAgentOutputParser()
    result = parser.parse_result([generation])
    assert result
    assert isinstance(result, AgentActionMessageLog)
    assert "arg1" in result.tool_input
    assert is_pyobject_tainted(result.tool_input["arg1"])


def test_llm_generate(iast_span_defaults):  # noqa: F811
    llm = FakeListLLM(responses=["I am a fake LLM"])
    prompt = prepare_tainted_prompt()
    result = llm.invoke(prompt)
    assert is_pyobject_tainted(result)


async def test_llm_agenerate(iast_span_defaults):  # noqa: F811
    llm = FakeListLLM(responses=["I am a fake LLM"])
    prompt = prepare_tainted_prompt()
    result = await llm.ainvoke(prompt)
    assert is_pyobject_tainted(result)


def test_chatmodel_generate(iast_span_defaults):  # noqa: F811
    chatmodel = FakeListChatModel(responses=["I am a fake chat model"])
    prompt = prepare_tainted_prompt()
    result = chatmodel.invoke(prompt)
    assert is_pyobject_tainted(result.content)


async def test_chatmodel_agenerate(iast_span_defaults):  # noqa: F811
    chatmodel = FakeListChatModel(responses=["I am a fake chat model"])
    prompt = prepare_tainted_prompt()
    result = await chatmodel.ainvoke(prompt)
    assert is_pyobject_tainted(result.content)


def test_cmdi_with_shelltool_invoke(iast_span_defaults):  # noqa: F811
    shell = ShellTool()
    cmd = taint_pyobject(
        pyobject="true",
        source_name="commands",
        source_value="true",
        source_origin=OriginType.PARAMETER,
    )

    # label test_cmdi_with_shelltool_invoke
    shell.invoke({"commands": cmd})

    span_report = _get_span_report()
    assert span_report


async def test_cmdi_with_shelltool_ainvoke(iast_span_defaults):  # noqa: F811
    shell = ShellTool()
    cmd = taint_pyobject(
        pyobject="true",
        source_name="commands",
        source_value="true",
        source_origin=OriginType.PARAMETER,
    )

    # label test_cmdi_with_shelltool_ainvoke
    await shell.ainvoke({"commands": cmd})

    span_report = _get_span_report()
    assert span_report


def test_cmdi_with_agent_invoke(iast_span_defaults):  # noqa: F811
    agent = prepare_cmdi_agent()
    prompt = prepare_tainted_prompt()
    # label test_cmdi_with_agent_invoke
    res = agent.invoke(prompt)
    assert res["output"] == "4"

    location = assert_cmdi()
    assert location["path"] == "tests/appsec/integrations/langchain_tests/test_iast_langchain.py"
    assert location["line"]
    assert location["method"] == "test_cmdi_with_agent_invoke"
    assert "class" not in location


async def test_cmdi_with_agent_ainvoke(iast_span_defaults):  # noqa: F811
    agent = prepare_cmdi_agent()
    prompt = prepare_tainted_prompt()
    # label test_cmdi_with_agent_ainvoke
    res = await agent.ainvoke(prompt)
    assert res["output"] == "4"

    location = assert_cmdi()
    # FIXME: Vulnerability here cannot be detected within the original user code, skip line and hash check.
    assert location["path"] == "langchain_experimental/llm_bash/bash.py"
    assert location["line"]
    assert location["method"] == "_run"
    assert "class" not in location


def test_openai_functions_agent_invoke(iast_span_defaults):  # noqa: F811
    ai_message = AIMessage(
        content=["Some content"],
        additional_kwargs=dict(
            function_call={"name": "my-tool", "arguments": taint_string(json.dumps({"arg1": "value1"}))}
        ),
    )
    llm = FakeOpenAILLM(responses=[ChatGeneration(message=ai_message)])
    shell = ShellTool()
    prompt_template = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "You are a helpful AI assistant.",
            ),
            ("human", "{input}"),
            MessagesPlaceholder(variable_name="agent_scratchpad"),
        ]
    )
    agent = create_openai_functions_agent(llm=llm, tools=[shell], prompt=prompt_template)
    prompt = taint_string("I need to use the terminal tool to print a Hello World")
    # label test_cmdi_with_openai_functions_agent_invoke
    res = agent.invoke({"input": prompt, "intermediate_steps": []})
    assert isinstance(res, AgentActionMessageLog)
    assert res.tool == "my-tool"
    assert res.tool_input == {"arg1": "value1"}
    assert is_pyobject_tainted(res.tool_input["arg1"])


@pytest.mark.parametrize("llm_class", ["FakeOpenAILLM", "FakeStreamingOpenAILLM"])
def test_cmdi_with_openai_functions_agent_invoke(iast_span_defaults, llm_class):  # noqa: F811
    ai_message = AIMessage(
        content=["Some content"],
        additional_kwargs=dict(
            function_call={"name": "terminal", "arguments": json.dumps({"commands": ["echo Hello World"]})}
        ),
    )
    llm = globals()[llm_class](
        responses=[ChatGeneration(message=ai_message), ChatGeneration(message=AIMessage(content="END"))]
    )
    shell = ShellTool()
    prompt_template = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "You are a helpful AI assistant.",
            ),
            ("human", "{input}"),
            MessagesPlaceholder(variable_name="agent_scratchpad"),
        ]
    )
    agent = create_openai_functions_agent(llm=llm, tools=[shell], prompt=prompt_template)
    agent_executor = AgentExecutor(agent=agent, tools=[shell], verbose=True)
    prompt = taint_string("I need to use the terminal tool to print a Hello World")
    # label test_cmdi_with_openai_functions_agent_invoke
    res = agent_executor.invoke({"input": prompt})
    assert isinstance(res, dict)
    assert res == {"input": prompt, "output": "END"}

    span_report = _get_span_report()
    assert span_report
    data = span_report.build_and_scrub_value_parts()
    vulnerability = data["vulnerabilities"][0]
    source = data["sources"][0]
    assert vulnerability["type"] == VULN_CMDI
    assert vulnerability["evidence"]["valueParts"] == [
        {"source": 0, "value": "echo "},
        {"pattern": "fghijklmnop", "redacted": True, "source": 0},
    ]
    assert "value" not in vulnerability["evidence"].keys()
    assert vulnerability["evidence"].get("pattern") is None
    assert vulnerability["evidence"].get("redacted") is None
    assert source["name"] == "commands"
    assert source["origin"] == OriginType.PARAMETER
    assert "value" not in source.keys()

    assert vulnerability["hash"]
    location = vulnerability["location"]
    assert location["path"] == "tests/appsec/integrations/langchain_tests/test_iast_langchain.py"
    assert location["line"]
    assert location["method"] == "test_cmdi_with_openai_functions_agent_invoke"
    assert "class" not in location


@pytest.mark.parametrize("llm_class", ["FakeOpenAILLM", "FakeStreamingOpenAILLM"])
async def test_cmdi_with_openai_functions_agent_ainvoke(iast_span_defaults, llm_class):  # noqa: F811
    ai_message = AIMessage(
        content=["Some content"],
        additional_kwargs=dict(
            function_call={"name": "terminal", "arguments": json.dumps({"commands": ["echo Hello World"]})}
        ),
    )
    llm = globals()[llm_class](
        responses=[ChatGeneration(message=ai_message), ChatGeneration(message=AIMessage(content="END"))]
    )
    shell = ShellTool()
    prompt_template = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "You are a helpful AI assistant.",
            ),
            ("human", "{input}"),
            MessagesPlaceholder(variable_name="agent_scratchpad"),
        ]
    )
    agent = create_openai_functions_agent(llm=llm, tools=[shell], prompt=prompt_template)
    agent_executor = AgentExecutor(agent=agent, tools=[shell], verbose=True)
    prompt = taint_string("I need to use the terminal tool to print a Hello World")
    # label test_cmdi_with_openai_functions_agent_ainvoke
    res = await agent_executor.ainvoke({"input": prompt})
    assert isinstance(res, dict)
    assert res == {"input": prompt, "output": "END"}

    span_report = _get_span_report()
    assert span_report
    data = span_report.build_and_scrub_value_parts()
    vulnerability = data["vulnerabilities"][0]
    source = data["sources"][0]
    assert vulnerability["type"] == VULN_CMDI
    assert vulnerability["evidence"]["valueParts"] == [
        {"source": 0, "value": "echo "},
        {"pattern": "fghijklmnop", "redacted": True, "source": 0},
    ]
    assert "value" not in vulnerability["evidence"].keys()
    assert vulnerability["evidence"].get("pattern") is None
    assert vulnerability["evidence"].get("redacted") is None
    assert source["name"] == "commands"
    assert source["origin"] == OriginType.PARAMETER
    assert "value" not in source.keys()

    assert vulnerability["hash"]
    location = vulnerability["location"]
    assert location["path"] == "langchain_experimental/llm_bash/bash.py"
    assert location["line"]
    assert location["method"] == "_run"
    assert "class" not in location


def prepare_cmdi_agent() -> AgentExecutor:
    responses = ["Action: terminal\nAction Input: echo Hello World", "Final Answer: 4"]
    llm = FakeListLLM(responses=responses)
    shell = ShellTool()
    return initialize_agent([shell], llm, agent=AgentType.ZERO_SHOT_REACT_DESCRIPTION, verbose=True)


def prepare_tainted_prompt() -> str:
    string_to_taint = "I need to use the terminal tool to print a Hello World"
    return taint_pyobject(
        pyobject=string_to_taint,
        source_name="prompt",
        source_value=string_to_taint,
        source_origin=OriginType.PARAMETER,
    )


def assert_cmdi():
    span_report = _get_span_report()
    assert span_report
    data = span_report.build_and_scrub_value_parts()
    vulnerability = data["vulnerabilities"][0]
    source = data["sources"][0]
    assert vulnerability["type"] == VULN_CMDI
    assert vulnerability["evidence"]["valueParts"] == [
        {"source": 0, "value": "echo "},
        {"pattern": "", "redacted": True, "source": 0},
        {"source": 0, "value": "Hello World"},
    ]
    assert "value" not in vulnerability["evidence"].keys()
    assert vulnerability["evidence"].get("pattern") is None
    assert vulnerability["evidence"].get("redacted") is None
    assert source["name"] == "prompt"
    assert source["origin"] == OriginType.PARAMETER
    assert "value" not in source.keys()

    assert vulnerability["hash"]
    return vulnerability["location"]


def taint_string(s: str) -> str:
    return taint_pyobject(
        pyobject=s,
        source_name="test",
        source_value=s,
        source_origin=OriginType.PARAMETER,
    )


class FakeOpenAILLM(BaseChatModel):
    """
    Similar to FakeListLLM but returns ChatGeneration instead of str. Useful to mock OpenAI LLM.
    """

    responses: list[ChatGeneration]

    iter: int = 0

    @property
    def _llm_type(self) -> str:
        return "openai"

    def _call(self, prompt: str, stop=None, run_manager=None, **kwargs) -> str:
        raise ValueError("Not implemented")

    def _generate(
        self,
        messages: list[BaseMessage],
        stop=None,
        run_manager=None,
        **kwargs,
    ) -> ChatResult:
        assert self.iter < len(self.responses), f"too many calls: iter={self.iter} messages={messages}"
        response = self.responses[self.iter]
        self.iter += 1
        return ChatResult(generations=[response])

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop=None,
        run_manager=None,
        **kwargs,
    ) -> ChatResult:
        return self._generate(messages)

    def _stream(
        self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs
    ) -> Iterator[ChatGenerationChunk]:
        chat_result = self._generate(messages)
        for gen in chat_result.generations:
            full_msg = gen.message
            msg_chunk = AIMessageChunk(content=full_msg.content, additional_kwargs=full_msg.additional_kwargs)
            gen_chunk = ChatGenerationChunk(message=msg_chunk)
            yield gen_chunk

    async def _astream(
        self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs
    ) -> AsyncIterator[ChatGenerationChunk]:
        for chunk in self._stream(messages):
            yield chunk


class FakeStreamingOpenAILLM(FakeOpenAILLM):
    """
    Variant of FakeOpenAILLM for streaming responses. This behaves close to ChatOpenAI.
    """

    def _stream(
        self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs
    ) -> Iterator[ChatGenerationChunk]:
        chat_result = self._generate(messages)
        for gen in chat_result.generations:
            full_msg = gen.message
            msg_chunk = AIMessageChunk(content=full_msg.content, additional_kwargs=full_msg.additional_kwargs)
            gen_chunk = ChatGenerationChunk(message=msg_chunk)
            yield gen_chunk

    async def _astream(
        self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs
    ) -> AsyncIterator[ChatGenerationChunk]:
        for chunk in self._stream(messages):
            yield chunk
