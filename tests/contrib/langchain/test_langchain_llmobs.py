import importlib
import json
from operator import itemgetter
import os
import sys

import mock
import pytest

from ddtrace import patch
from ddtrace.internal.utils.version import parse_version
from ddtrace.llmobs import LLMObs
from ddtrace.trace import Span
from tests.contrib.langchain.utils import mock_langchain_chat_generate_response
from tests.contrib.langchain.utils import mock_langchain_llm_generate_response
from tests.llmobs._utils import _expected_llmobs_llm_span_event
from tests.llmobs._utils import _expected_llmobs_non_llm_span_event
from tests.llmobs._utils import iterate_stream
from tests.llmobs._utils import next_stream
from tests.subprocesstest import SubprocessTestCase
from tests.subprocesstest import run_in_subprocess


# Multi-message prompt template test constants

PROMPT_TEMPLATE_EXPECTED_CHAT_TEMPLATE = [
    {"role": "system", "content": "You are a {role} assistant."},
    {"role": "system", "content": "Your expertise is in {domain}."},
    {"role": "system", "content": "Additional context: {context}"},
    {"role": "user", "content": "I'm a user seeking help."},
    {"role": "user", "content": "Please help with {task}"},
    {"role": "user", "content": "Specifically, I need {specific_help}"},
    {"role": "assistant", "content": "I understand your request."},
    {"role": "assistant", "content": "I'll help you with {task}."},
    {"role": "assistant", "content": "Let me provide {output_type}"},
    {"role": "developer", "content": "Make it {style} and under {limit} words"},
]


def _create_multi_message_prompt_template(langchain_core, metadata=None):
    """Helper function to create multi-message ChatPromptTemplate with mixed input types."""
    from langchain_core.messages import AIMessage
    from langchain_core.messages import HumanMessage
    from langchain_core.messages import SystemMessage
    from langchain_core.prompts import AIMessagePromptTemplate
    from langchain_core.prompts import ChatMessagePromptTemplate
    from langchain_core.prompts import HumanMessagePromptTemplate
    from langchain_core.prompts import SystemMessagePromptTemplate

    messages = [
        SystemMessage(content="You are a {role} assistant."),  # while this has handlebars, it is not a template
        ("system", "Your expertise is in {domain}."),
        SystemMessagePromptTemplate.from_template("Additional context: {context}"),
        HumanMessage(content="I'm a user seeking help."),
        ("human", "Please help with {task}"),
        HumanMessagePromptTemplate.from_template("Specifically, I need {specific_help}"),
        AIMessage(content="I understand your request."),
        ("ai", "I'll help you with {task}."),
        AIMessagePromptTemplate.from_template("Let me provide {output_type}"),
        ChatMessagePromptTemplate.from_template("Make it {style} and under {limit} words", role="developer"),
    ]

    template = langchain_core.prompts.ChatPromptTemplate.from_messages(messages=messages)
    template.metadata = metadata
    return template


def _expected_langchain_llmobs_llm_span(
    span, input_role=None, mock_io=False, mock_token_metrics=False, span_links=False, metadata=None, prompt=None
):
    provider = span.get_tag("langchain.request.provider")

    metadata = metadata if metadata else mock.ANY

    input_messages = [{"content": mock.ANY}]
    output_messages = [{"content": mock.ANY}]
    if input_role is not None:
        input_messages[0]["role"] = input_role
        output_messages[0]["role"] = "assistant"

    metrics = (
        {"input_tokens": mock.ANY, "output_tokens": mock.ANY, "total_tokens": mock.ANY} if mock_token_metrics else {}
    )

    return _expected_llmobs_llm_span_event(
        span,
        model_name=span.get_tag("langchain.request.model"),
        model_provider=provider,
        input_messages=input_messages if not mock_io else mock.ANY,
        output_messages=output_messages if not mock_io else mock.ANY,
        metadata=metadata,
        token_metrics=metrics,
        tags={"ml_app": "langchain_test", "service": "tests.contrib.langchain"},
        span_links=span_links,
        prompt=prompt,
    )


def _expected_langchain_llmobs_chain_span(span, input_value=None, output_value=None, span_links=False, metadata=None):
    metadata = metadata if metadata else mock.ANY
    return _expected_llmobs_non_llm_span_event(
        span,
        "workflow",
        input_value=input_value if input_value is not None else mock.ANY,
        output_value=output_value if output_value is not None else mock.ANY,
        tags={"ml_app": "langchain_test", "service": "tests.contrib.langchain"},
        span_links=span_links,
        metadata=metadata,
    )


def test_llmobs_openai_llm(langchain_openai, llmobs_events, tracer, openai_url):
    llm = langchain_openai.OpenAI(base_url=openai_url, max_tokens=256)
    llm.invoke("Can you explain what Descartes meant by 'I think, therefore I am'?")

    span = tracer.pop_traces()[0][0]
    assert len(llmobs_events) == 1
    assert llmobs_events[0] == _expected_langchain_llmobs_llm_span(
        span, mock_token_metrics=True, metadata={"max_tokens": 256, "temperature": 0.7}
    )


@mock.patch("langchain_core.language_models.llms.BaseLLM._generate_helper")
def test_llmobs_openai_llm_proxy(mock_generate, langchain_openai, llmobs_events, tracer):
    mock_generate.return_value = mock_langchain_llm_generate_response
    llm = langchain_openai.OpenAI(base_url="http://localhost:4000", model="gpt-3.5-turbo")
    llm.invoke("What is the capital of France?")

    span = tracer.pop_traces()[0][0]
    assert len(llmobs_events) == 1
    assert llmobs_events[0] == _expected_langchain_llmobs_chain_span(
        span,
        input_value=json.dumps([{"content": "What is the capital of France?"}]),
    )

    # span created from request with non-proxy URL should result in an LLM span
    llm = langchain_openai.OpenAI(base_url="http://localhost:8000", model="gpt-3.5-turbo")
    llm.invoke("What is the capital of France?")
    span = tracer.pop_traces()[0][0]
    assert len(llmobs_events) == 2
    assert llmobs_events[1]["meta"]["span"]["kind"] == "llm"


def test_llmobs_openai_chat_model(langchain_core, langchain_openai, llmobs_events, tracer, openai_url):
    chat_model = langchain_openai.ChatOpenAI(temperature=0, max_tokens=256, base_url=openai_url)
    chat_model.invoke([langchain_core.messages.HumanMessage(content="When do you use 'who' instead of 'whom'?")])

    span = tracer.pop_traces()[0][0]
    assert len(llmobs_events) == 1
    assert llmobs_events[0] == _expected_langchain_llmobs_llm_span(
        span,
        input_role="user",
        mock_token_metrics=True,
        metadata={"temperature": 0.0, "max_tokens": 256},
    )


def test_llmobs_openai_chat_model_no_usage(langchain_core, langchain_openai, llmobs_events, openai_url):
    if parse_version(importlib.metadata.version("langchain_openai")) < (0, 2, 0):
        pytest.skip("langchain-openai <0.2.0 does not support stream_usage=False")
    chat_model = langchain_openai.ChatOpenAI(temperature=0, max_tokens=256, base_url=openai_url, stream_usage=False)

    response = chat_model.stream(
        [langchain_core.messages.HumanMessage(content="When do you use 'who' instead of 'whom'?")]
    )
    for _ in response:
        pass

    assert len(llmobs_events) == 1
    assert llmobs_events[0]["metrics"].get("input_tokens") is None
    assert llmobs_events[0]["metrics"].get("output_tokens") is None
    assert llmobs_events[0]["metrics"].get("total_tokens") is None


@mock.patch("langchain_core.language_models.chat_models.BaseChatModel._generate_with_cache")
def test_llmobs_openai_chat_model_proxy(mock_generate, langchain_core, langchain_openai, llmobs_events, tracer):
    mock_generate.return_value = mock_langchain_chat_generate_response
    chat_model = langchain_openai.ChatOpenAI(temperature=0, max_tokens=256, base_url="http://localhost:4000")
    chat_model.invoke([langchain_core.messages.HumanMessage(content="What is the capital of France?")])

    span = tracer.pop_traces()[0][0]
    assert len(llmobs_events) == 1
    assert llmobs_events[0] == _expected_langchain_llmobs_chain_span(
        span,
        input_value=json.dumps([{"content": "What is the capital of France?", "role": "user"}]),
        metadata={"temperature": 0.0, "max_tokens": 256},
    )

    # span created from request with non-proxy URL should result in an LLM span
    chat_model = langchain_openai.ChatOpenAI(temperature=0, max_tokens=256, base_url="http://localhost:8000")
    chat_model.invoke([langchain_core.messages.HumanMessage(content="What is the capital of France?")])
    span = tracer.pop_traces()[0][0]
    assert len(llmobs_events) == 2
    assert llmobs_events[1]["meta"]["span"]["kind"] == "llm"


def test_llmobs_string_prompt_template_invoke(langchain_core, langchain_openai, openai_url, llmobs_events, tracer):
    template_string = "You are a helpful assistant. Please answer this question: {question}"
    variable_dict = {"question": "What is machine learning?"}
    prompt_template = langchain_core.prompts.PromptTemplate(
        input_variables=list(variable_dict.keys()),
        template=template_string,
        metadata={"test_type": "basic_invoke", "author": "test_suite"},
    )
    llm = langchain_openai.OpenAI(base_url=openai_url)
    chain = prompt_template | llm
    chain.invoke(variable_dict)

    llmobs_events.sort(key=lambda span: span["start_ns"])
    assert len(llmobs_events) == 2
    actual_prompt = llmobs_events[1]["meta"]["input"]["prompt"]
    assert actual_prompt["id"] == "test_langchain_llmobs.prompt_template"
    assert actual_prompt["template"] == template_string
    assert actual_prompt["variables"] == variable_dict
    # Check that metadata from the prompt template is preserved
    assert "tags" in actual_prompt
    assert actual_prompt["tags"] == {"test_type": "basic_invoke", "author": "test_suite"}


def test_llmobs_string_prompt_template_direct_invoke(
    langchain_core, langchain_openai, openai_url, llmobs_events, tracer
):
    """Test StringPromptTemplate (PromptTemplate) with variable name detection using direct invoke (no chains)."""
    template_string = "Good {time_of_day}, {name}! How are you doing today?"
    variable_dict = {"name": "Alice", "time_of_day": "morning"}
    greeting_template = langchain_core.prompts.PromptTemplate(
        input_variables=list(variable_dict.keys()),
        template=template_string,
        metadata={"test_type": "direct_invoke", "interaction": "greeting", "not_string_1": True, "not_string_2": 10},
    )
    llm = langchain_openai.OpenAI(base_url=openai_url)

    # Direct invoke on template first, then pass to LLM (no chain)
    prompt_value = greeting_template.invoke(variable_dict)
    llm.invoke(prompt_value)

    llmobs_events.sort(key=lambda span: span["start_ns"])
    assert len(llmobs_events) == 1  # Only LLM span, prompt template invoke doesn't create LLMObs event by itself

    # The prompt should be attached to the LLM span
    actual_prompt = llmobs_events[0]["meta"]["input"]["prompt"]
    assert actual_prompt["id"] == "test_langchain_llmobs.greeting_template"
    assert actual_prompt["template"] == template_string
    assert actual_prompt["variables"] == variable_dict
    # Check that metadata from the prompt template is preserved
    assert "tags" in actual_prompt
    assert actual_prompt["tags"] == {"test_type": "direct_invoke", "interaction": "greeting"}


def test_llmobs_string_prompt_template_invoke_chat_model(
    langchain_core, langchain_openai, openai_url, llmobs_events, tracer
):
    template_string = "You are a helpful assistant. Please answer this question: {question}"
    variable_dict = {"question": "What is machine learning?"}
    prompt_template = langchain_core.prompts.PromptTemplate(
        input_variables=list(variable_dict.keys()), template=template_string
    )
    chat_model = langchain_openai.ChatOpenAI(base_url=openai_url)
    chain = prompt_template | chat_model
    chain.invoke(variable_dict)

    llmobs_events.sort(key=lambda span: span["start_ns"])
    assert len(llmobs_events) == 2
    actual_prompt = llmobs_events[1]["meta"]["input"]["prompt"]
    assert actual_prompt["id"] == "test_langchain_llmobs.prompt_template"
    assert actual_prompt["template"] == template_string
    assert actual_prompt["variables"] == variable_dict


def test_llmobs_string_prompt_template_single_variable_string_input(
    langchain_core, langchain_openai, openai_url, llmobs_events, tracer
):
    template_string = "Write a creative story about {topic}."
    single_variable_template = langchain_core.prompts.PromptTemplate(
        input_variables=["topic"], template=template_string
    )
    llm = langchain_openai.OpenAI(base_url=openai_url)
    chain = single_variable_template | llm

    # Pass string input directly instead of dict for single variable template
    string_input = "time travel"
    chain.invoke(string_input)

    llmobs_events.sort(key=lambda span: span["start_ns"])
    assert len(llmobs_events) == 2
    actual_prompt = llmobs_events[1]["meta"]["input"]["prompt"]
    assert actual_prompt["id"] == "test_langchain_llmobs.single_variable_template"
    assert actual_prompt["template"] == template_string
    # Should convert string input to dict format with single variable
    assert actual_prompt["variables"] == {"topic": "time travel"}


def test_llmobs_multi_message_prompt_template_sync_chain(
    langchain_core, langchain_openai, openai_url, llmobs_events, tracer
):
    test_metadata = {"template_type": "multi_message", "test_scenario": "sync_chain", "message_count": 10}
    multi_message_template = _create_multi_message_prompt_template(langchain_core, metadata=test_metadata)
    llm = langchain_openai.ChatOpenAI(base_url=openai_url)
    chain = multi_message_template | llm

    variable_dict = {
        "domain": "creative writing",
        "context": "focus on storytelling",
        "task": "writing a short story",
        "specific_help": "character development",
        "output_type": "guidance",
        "style": "engaging",
        "limit": "100",
    }

    chain.invoke(variable_dict)

    llmobs_events.sort(key=lambda span: span["start_ns"])
    assert len(llmobs_events) == 2

    # Verify the prompt structure in the LLM span
    actual_prompt = llmobs_events[1]["meta"]["input"]["prompt"]
    assert actual_prompt["id"] == "test_langchain_llmobs.multi_message_template"
    assert actual_prompt["variables"] == variable_dict
    assert actual_prompt.get("template") is None
    assert actual_prompt["chat_template"] == PROMPT_TEMPLATE_EXPECTED_CHAT_TEMPLATE
    # Check that metadata from the multi-message prompt template is preserved
    assert "tags" in actual_prompt
    assert actual_prompt["tags"] == {k: v for k, v in test_metadata.items() if isinstance(v, str)}


def test_llmobs_multi_message_prompt_template_sync_direct_invoke(
    langchain_core, langchain_openai, openai_url, llmobs_events, tracer
):
    multi_message_template = _create_multi_message_prompt_template(langchain_core)
    llm = langchain_openai.ChatOpenAI(base_url=openai_url)

    variable_dict = {
        "domain": "data analysis",
        "context": "focus on accuracy",
        "task": "analyzing datasets",
        "specific_help": "statistical methods",
        "output_type": "insights",
        "style": "detailed",
        "limit": "150",
    }

    # Test direct invoke on template then LLM (no chain)
    prompt_value = multi_message_template.invoke(variable_dict)
    llm.invoke(prompt_value)

    llmobs_events.sort(key=lambda span: span["start_ns"])
    assert len(llmobs_events) == 1  # Only LLM span for direct invoke

    # Verify the prompt structure
    actual_prompt = llmobs_events[0]["meta"]["input"]["prompt"]
    assert actual_prompt["id"] == "test_langchain_llmobs.multi_message_template"
    assert actual_prompt["variables"] == variable_dict
    assert actual_prompt.get("template") is None
    assert actual_prompt["chat_template"] == PROMPT_TEMPLATE_EXPECTED_CHAT_TEMPLATE


@pytest.mark.asyncio
async def test_llmobs_multi_message_prompt_template_async_direct_invoke(
    langchain_core, langchain_openai, openai_url, llmobs_events, tracer
):
    multi_message_template = _create_multi_message_prompt_template(langchain_core)
    llm = langchain_openai.ChatOpenAI(base_url=openai_url)

    variable_dict = {
        "domain": "software engineering",
        "context": "focus on best practices",
        "task": "code review",
        "specific_help": "performance optimization",
        "output_type": "recommendations",
        "style": "thorough",
        "limit": "200",
    }

    # Test direct async invoke on template then LLM
    prompt_value = await multi_message_template.ainvoke(variable_dict)
    await llm.ainvoke(prompt_value)

    llmobs_events.sort(key=lambda span: span["start_ns"])
    assert len(llmobs_events) == 1  # Only LLM span for direct invoke

    # Verify the prompt structure
    actual_prompt = llmobs_events[0]["meta"]["input"]["prompt"]
    assert actual_prompt["id"] == "test_langchain_llmobs.multi_message_template"
    assert actual_prompt["variables"] == variable_dict
    assert actual_prompt.get("template") is None
    assert actual_prompt["chat_template"] == PROMPT_TEMPLATE_EXPECTED_CHAT_TEMPLATE


def test_llmobs_chain(langchain_core, langchain_openai, openai_url, llmobs_events, tracer):
    prompt = langchain_core.prompts.ChatPromptTemplate.from_messages(
        [("system", "You are world class technical documentation writer."), ("user", "{input}")]
    )
    chain = prompt | langchain_openai.OpenAI(base_url=openai_url, max_tokens=256)
    chain.invoke({"input": "Can you explain what an LLM chain is?"})

    llmobs_events.sort(key=lambda span: span["start_ns"])
    trace = tracer.pop_traces()[0]
    assert len(llmobs_events) == 2
    assert llmobs_events[0] == _expected_langchain_llmobs_chain_span(
        trace[0],
        input_value=json.dumps([{"input": "Can you explain what an LLM chain is?"}]),
        span_links=True,
    )
    assert llmobs_events[1] == _expected_langchain_llmobs_llm_span(
        trace[1],
        mock_token_metrics=True,
        span_links=True,
        metadata={"max_tokens": 256, "temperature": 0.7},
        prompt={
            "id": "test_langchain_llmobs.prompt",
            "chat_template": [
                {"content": "You are world class technical documentation writer.", "role": "system"},
                {"content": "{input}", "role": "user"},
            ],
            "variables": {"input": "Can you explain what an LLM chain is?"},
            "version": "0.0.0",
            "_dd_context_variable_keys": ["context"],
            "_dd_query_variable_keys": ["question"],
        },
    )


def test_llmobs_chain_nested(langchain_core, langchain_openai, openai_url, llmobs_events, tracer):
    prompt1 = langchain_core.prompts.ChatPromptTemplate.from_template("what is the city {person} is from?")
    prompt2 = langchain_core.prompts.ChatPromptTemplate.from_template(
        "what country is the city {city} in? respond in {language}"
    )
    model = langchain_openai.OpenAI(base_url=openai_url)
    chain1 = prompt1 | model | langchain_core.output_parsers.StrOutputParser()
    chain2 = prompt2 | model | langchain_core.output_parsers.StrOutputParser()
    complete_chain = {"city": chain1, "language": itemgetter("language")} | chain2

    complete_chain.invoke({"person": "Spongebob Squarepants", "language": "Spanish"})

    llmobs_events.sort(key=lambda span: span["start_ns"])
    trace = tracer.pop_traces()[0]
    assert len(llmobs_events) == 4
    assert llmobs_events[0] == _expected_langchain_llmobs_chain_span(
        trace[0],
        input_value=json.dumps([{"person": "Spongebob Squarepants", "language": "Spanish"}]),
        output_value=mock.ANY,
        span_links=True,
    )
    assert llmobs_events[1] == _expected_langchain_llmobs_chain_span(
        trace[1],
        input_value=json.dumps([{"person": "Spongebob Squarepants", "language": "Spanish"}]),
        output_value=mock.ANY,
        span_links=True,
    )
    assert llmobs_events[2] == _expected_langchain_llmobs_llm_span(
        trace[2],
        mock_token_metrics=True,
        span_links=True,
        prompt={
            "id": "langchain.unknown_prompt_template",
            "chat_template": [{"content": "what is the city {person} is from?", "role": "user"}],
            "variables": {"person": "Spongebob Squarepants", "language": "Spanish"},
            "version": "0.0.0",
            "_dd_context_variable_keys": ["context"],
            "_dd_query_variable_keys": ["question"],
        },
    )
    assert llmobs_events[3] == _expected_langchain_llmobs_llm_span(
        trace[3],
        mock_token_metrics=True,
        span_links=True,
        prompt={
            "id": "test_langchain_llmobs.prompt2",
            "chat_template": [{"content": "what country is the city {city} in? respond in {language}", "role": "user"}],
            "variables": {"city": mock.ANY, "language": "Spanish"},
            "version": "0.0.0",
            "_dd_context_variable_keys": ["context"],
            "_dd_query_variable_keys": ["question"],
        },
    )


@pytest.mark.skipif(sys.version_info >= (3, 11), reason="Python <3.11 required")
def test_llmobs_chain_batch(langchain_core, langchain_openai, llmobs_events, tracer, openai_url):
    prompt = langchain_core.prompts.ChatPromptTemplate.from_template("Tell me a short joke about {topic}")
    output_parser = langchain_core.output_parsers.StrOutputParser()
    model = langchain_openai.ChatOpenAI(base_url=openai_url)
    chain = {"topic": langchain_core.runnables.RunnablePassthrough()} | prompt | model | output_parser

    chain.batch(inputs=["chickens", "pigs"])

    llmobs_events.sort(key=lambda span: span["start_ns"])
    trace = tracer.pop_traces()[0]
    assert len(llmobs_events) == 3
    assert llmobs_events[0] == _expected_langchain_llmobs_chain_span(
        trace[0],
        input_value=json.dumps(["chickens", "pigs"]),
        output_value=mock.ANY,
        span_links=True,
    )

    try:
        assert llmobs_events[1] == _expected_langchain_llmobs_llm_span(
            trace[1],
            input_role="user",
            mock_token_metrics=True,
            span_links=True,
            prompt={
                "id": "langchain.unknown_prompt_template",
                "chat_template": [{"content": "Tell me a short joke about {topic}", "role": "user"}],
                "variables": {"topic": "chickens"},
                "version": "0.0.0",
                "_dd_context_variable_keys": ["context"],
                "_dd_query_variable_keys": ["question"],
            },
        )
        assert llmobs_events[2] == _expected_langchain_llmobs_llm_span(
            trace[2],
            input_role="user",
            mock_token_metrics=True,
            span_links=True,
            prompt={
                "id": "langchain.unknown_prompt_template",
                "chat_template": [{"content": "Tell me a short joke about {topic}", "role": "user"}],
                "variables": {"topic": "pigs"},
                "version": "0.0.0",
                "_dd_context_variable_keys": ["context"],
                "_dd_query_variable_keys": ["question"],
            },
        )
    except AssertionError:
        assert llmobs_events[1] == _expected_langchain_llmobs_llm_span(
            trace[2],
            input_role="user",
            mock_token_metrics=True,
            span_links=True,
            prompt={
                "id": "langchain.unknown_prompt_template",
                "chat_template": [{"content": "Tell me a short joke about {topic}", "role": "user"}],
                "variables": {"topic": "chickens"},
                "version": "0.0.0",
                "_dd_context_variable_keys": ["context"],
                "_dd_query_variable_keys": ["question"],
            },
        )
        assert llmobs_events[2] == _expected_langchain_llmobs_llm_span(
            trace[1],
            input_role="user",
            mock_token_metrics=True,
            span_links=True,
            prompt={
                "id": "langchain.unknown_prompt_template",
                "chat_template": [{"content": "Tell me a short joke about {topic}", "role": "user"}],
                "variables": {"topic": "pigs"},
                "version": "0.0.0",
                "_dd_context_variable_keys": ["context"],
                "_dd_query_variable_keys": ["question"],
            },
        )


def test_llmobs_chain_schema_io(langchain_core, langchain_openai, openai_url, llmobs_events, tracer):
    prompt = langchain_core.prompts.ChatPromptTemplate.from_messages(
        [
            ("system", "You're an assistant who's good at {ability}. Respond in 20 words or fewer"),
            langchain_core.prompts.MessagesPlaceholder(variable_name="history"),
            ("human", "{input}"),
        ]
    )
    chain = prompt | langchain_openai.ChatOpenAI(base_url=openai_url)

    chain.invoke(
        {
            "ability": "world capitals",
            "history": [
                langchain_core.messages.HumanMessage(content="Can you be my science teacher instead?"),
                langchain_core.messages.AIMessage(content="Yes"),
            ],
            "input": "What's the powerhouse of the cell?",
        }
    )

    llmobs_events.sort(key=lambda span: span["start_ns"])
    trace = tracer.pop_traces()[0]
    assert len(llmobs_events) == 2
    assert llmobs_events[0] == _expected_langchain_llmobs_chain_span(
        trace[0],
        input_value=json.dumps(
            [
                {
                    "ability": "world capitals",
                    "history": [["user", "Can you be my science teacher instead?"], ["assistant", "Yes"]],
                    "input": "What's the powerhouse of the cell?",
                }
            ]
        ),
        output_value=json.dumps(["assistant", "Mitochondria"]),
        span_links=True,
    )
    assert llmobs_events[1] == _expected_langchain_llmobs_llm_span(
        trace[1],
        mock_io=True,
        mock_token_metrics=True,
        span_links=True,
    )


def test_llmobs_anthropic_chat_model(langchain_anthropic, llmobs_events, tracer, anthropic_url):
    kwargs = dict(
        temperature=0,
        max_tokens=15,
        model_name="claude-3-opus-20240229",
    )

    if "anthropic_api_url" in langchain_anthropic.ChatAnthropic.__fields__:
        kwargs["anthropic_api_url"] = anthropic_url
    else:
        kwargs["base_url"] = anthropic_url

    chat = langchain_anthropic.ChatAnthropic(**kwargs)
    chat.invoke("When do you use 'whom' instead of 'who'?")

    span = tracer.pop_traces()[0][0]
    assert len(llmobs_events) == 1
    assert llmobs_events[0] == _expected_langchain_llmobs_llm_span(
        span,
        input_role="user",
        mock_token_metrics=True,
        metadata={"temperature": 0, "max_tokens": 15},
    )


def test_llmobs_embedding_documents(langchain_openai, llmobs_events, tracer, openai_url):
    embedding_model = langchain_openai.embeddings.OpenAIEmbeddings(base_url=openai_url)
    embedding_model.embed_documents(["hello world", "goodbye world"])

    trace = tracer.pop_traces()[0]
    assert len(llmobs_events) == 1
    assert llmobs_events[0] == _expected_llmobs_llm_span_event(
        trace[0],
        span_kind="embedding",
        model_name="text-embedding-ada-002",
        model_provider="openai",
        input_documents=[{"text": "hello world"}, {"text": "goodbye world"}],
        output_value="[2 embedding(s) returned with size 1536]",
        tags={"ml_app": "langchain_test", "service": "tests.contrib.langchain"},
    )


def test_llmobs_embedding_query(langchain_openai, llmobs_events, tracer, openai_url):
    embedding_model = langchain_openai.embeddings.OpenAIEmbeddings(base_url=openai_url)
    embedding_model.embed_query("hello world")

    trace = tracer.pop_traces()[0]
    assert len(llmobs_events) == 1
    assert llmobs_events[0] == _expected_llmobs_llm_span_event(
        trace[0],
        span_kind="embedding",
        model_name=embedding_model.model,
        model_provider="openai",
        input_documents=[{"text": "hello world"}],
        output_value="[1 embedding(s) returned with size 1536]",
        tags={"ml_app": "langchain_test", "service": "tests.contrib.langchain"},
    )


def test_llmobs_vectorstore_similarity_search(langchain_in_memory_vectorstore, llmobs_events, tracer):
    vectorstore = langchain_in_memory_vectorstore
    vectorstore.similarity_search("France", k=1)

    trace = tracer.pop_traces()[0]
    assert len(llmobs_events) == 2
    llmobs_events.sort(key=lambda span: span["start_ns"])
    expected_span = _expected_llmobs_non_llm_span_event(
        trace[0],
        "retrieval",
        input_value="France",
        output_documents=[{"text": mock.ANY, "id": mock.ANY, "name": mock.ANY}],
        output_value="[1 document(s) retrieved]",
        tags={"ml_app": "langchain_test", "service": "tests.contrib.langchain"},
        span_links=True,
    )
    assert llmobs_events[0] == expected_span


def test_llmobs_chat_model_tool_calls(langchain_core, langchain_openai, llmobs_events, tracer, openai_url):
    @langchain_core.tools.tool
    def add(a: int, b: int) -> int:
        """Adds a and b.
        Args:
            a: first int
            b: second int
        """
        return a + b

    llm = langchain_openai.ChatOpenAI(model="gpt-3.5-turbo-0125", temperature=0.7, base_url=openai_url)
    llm_with_tools = llm.bind_tools([add])
    llm_with_tools.invoke([langchain_core.messages.HumanMessage(content="What is the sum of 1 and 2?")])

    span = tracer.pop_traces()[0][0]
    assert len(llmobs_events) == 1
    assert llmobs_events[0] == _expected_llmobs_llm_span_event(
        span,
        model_name=span.get_tag("langchain.request.model"),
        model_provider=span.get_tag("langchain.request.provider"),
        input_messages=[{"role": "user", "content": "What is the sum of 1 and 2?"}],
        output_messages=[
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "name": "add",
                        "arguments": {"a": 1, "b": 2},
                        "tool_id": mock.ANY,
                    }
                ],
            }
        ],
        metadata={"temperature": 0.7},
        token_metrics={"input_tokens": mock.ANY, "output_tokens": mock.ANY, "total_tokens": mock.ANY},
        tags={"ml_app": "langchain_test", "service": "tests.contrib.langchain"},
    )


def test_llmobs_base_tool_invoke(langchain_core, llmobs_events, tracer):
    from math import pi

    def circumference_tool(radius: float) -> float:
        return float(radius) * 2.0 * pi

    calculator = langchain_core.tools.StructuredTool.from_function(
        func=circumference_tool,
        name="Circumference calculator",
        description="Use this tool when you need to calculate a circumference using the radius of a circle",
        return_direct=True,
        response_format="content",
    )

    calculator.invoke("2", config={"test": "this is to test config"})

    span = tracer.pop_traces()[0][0]
    assert len(llmobs_events) == 1
    assert llmobs_events[0] == _expected_llmobs_non_llm_span_event(
        span,
        span_kind="tool",
        input_value="2",
        output_value="12.566370614359172",
        metadata={
            "tool_config": {"test": "this is to test config"},
            "tool_info": {
                "name": "Circumference calculator",
                "description": mock.ANY,
            },
        },
        tags={"ml_app": "langchain_test", "service": "tests.contrib.langchain"},
    )


@pytest.mark.parametrize("consume_stream", [iterate_stream, next_stream])
def test_llmobs_streamed_chain(langchain_core, langchain_openai, llmobs_events, tracer, openai_url, consume_stream):
    prompt = langchain_core.prompts.ChatPromptTemplate.from_messages(
        [("system", "You are a world class technical documentation writer."), ("user", "{input}")]
    )
    llm = langchain_openai.ChatOpenAI(model="gpt-4o", base_url=openai_url, temperature=0.7, max_tokens=256)
    parser = langchain_core.output_parsers.StrOutputParser()

    chain = prompt | llm | parser

    consume_stream(chain.stream({"input": "how can langsmith help with testing?"}))

    trace = tracer.pop_traces()[0]
    assert len(llmobs_events) == 2
    llmobs_events.sort(key=lambda span: span["start_ns"])
    assert llmobs_events[0] == _expected_langchain_llmobs_chain_span(
        trace[0],
        input_value=json.dumps({"input": "how can langsmith help with testing?"}),
        output_value=mock.ANY,
        span_links=True,
    )
    assert llmobs_events[1] == _expected_llmobs_llm_span_event(
        trace[1],
        model_name=trace[1].get_tag("langchain.request.model"),
        model_provider=trace[1].get_tag("langchain.request.provider"),
        input_messages=[
            {"content": "You are a world class technical documentation writer.", "role": "SystemMessage"},
            {"content": "how can langsmith help with testing?", "role": "HumanMessage"},
        ],
        output_messages=[{"content": mock.ANY, "role": "assistant"}],
        metadata={"temperature": 0.7, "max_tokens": 256},
        token_metrics={},
        tags={"ml_app": "langchain_test", "service": "tests.contrib.langchain"},
        span_links=True,
    )


@pytest.mark.parametrize("consume_stream", [iterate_stream, next_stream])
def test_llmobs_streamed_llm(langchain_openai, llmobs_events, tracer, openai_url, consume_stream):
    llm = langchain_openai.OpenAI(base_url=openai_url, n=1, seed=1, logprobs=None, logit_bias=None)

    consume_stream(llm.stream("What is 2+2?\n\n"))
    span = tracer.pop_traces()[0][0]
    assert len(llmobs_events) == 1
    assert llmobs_events[0] == _expected_llmobs_llm_span_event(
        span,
        model_name=span.get_tag("langchain.request.model"),
        model_provider=span.get_tag("langchain.request.provider"),
        input_messages=[
            {"content": "What is 2+2?\n\n"},
        ],
        output_messages=[{"content": "The answer is 4."}],
        metadata={"temperature": 0.7, "max_tokens": 256},
        token_metrics={},
        tags={"ml_app": "langchain_test", "service": "tests.contrib.langchain"},
    )


def test_llmobs_non_ascii_completion(langchain_openai, openai_url, llmobs_events):
    llm = langchain_openai.OpenAI(base_url=openai_url)
    llm.invoke("안녕,\n 지금 몇 시야?")

    assert len(llmobs_events) == 1
    actual_llmobs_span_event = llmobs_events[0]
    assert actual_llmobs_span_event["meta"]["input"]["messages"][0]["content"] == "안녕,\n 지금 몇 시야?"


def test_llmobs_set_tags_with_none_response(langchain_core):
    integration = langchain_core._datadog_integration
    integration._llmobs_set_tags(
        span=Span("test_span", service="test_service"),
        args=[],
        kwargs={},
        response=None,
        operation="chat",
    )


class TestTraceStructureWithLLMIntegrations(SubprocessTestCase):
    bedrock_env_config = dict(
        AWS_ACCESS_KEY_ID="testing",
        AWS_SECRET_ACCESS_KEY="testing",
        AWS_SECURITY_TOKEN="testing",
        AWS_SESSION_TOKEN="testing",
        AWS_DEFAULT_REGION="us-east-1",
        DD_LANGCHAIN_METRICS_ENABLED="false",
        DD_API_KEY="<not-a-real-key>",
    )

    openai_env_config = dict(
        OPENAI_API_KEY=os.getenv("OPENAI_API_KEY", "testing"),
        DD_API_KEY="<not-a-real-key>",
    )

    anthropic_env_config = dict(
        ANTHROPIC_API_KEY=os.getenv("ANTHROPIC_API_KEY", "testing"),
        DD_API_KEY="<not-a-real-key>",
    )

    def setUp(self):
        patcher = mock.patch("ddtrace.llmobs._llmobs.LLMObsSpanWriter")
        LLMObsSpanWriterMock = patcher.start()
        mock_llmobs_span_writer = mock.MagicMock()
        LLMObsSpanWriterMock.return_value = mock_llmobs_span_writer

        self.mock_llmobs_span_writer = mock_llmobs_span_writer

        super(TestTraceStructureWithLLMIntegrations, self).setUp()

    def tearDown(self):
        LLMObs.disable()

    def _assert_trace_structure_from_writer_call_args(self, span_kinds):
        assert self.mock_llmobs_span_writer.enqueue.call_count == len(span_kinds)

        calls = self.mock_llmobs_span_writer.enqueue.call_args_list[::-1]

        for span_kind, call in zip(span_kinds, calls):
            call_args = call.args[0]

            assert (
                call_args["meta"]["span"]["kind"] == span_kind
            ), f"Span kind is {call_args['meta']['span']['kind']} but expected {span_kind}"
            if span_kind == "workflow":
                assert len(call_args["meta"]["input"]["value"]) > 0
                assert len(call_args["meta"]["output"]["value"]) > 0
            elif span_kind == "llm":
                assert len(call_args["meta"]["input"]["messages"]) > 0
                assert len(call_args["meta"]["output"]["messages"]) > 0
            elif span_kind == "embedding":
                assert len(call_args["meta"]["input"]["documents"]) > 0
                assert len(call_args["meta"]["output"]["value"]) > 0

    @staticmethod
    def _call_openai_llm(OpenAI):
        llm = OpenAI(base_url="http://localhost:9126/vcr/openai")
        llm.invoke("Can you explain what Descartes meant by 'I think, therefore I am'?")

    @staticmethod
    def _call_openai_embedding(OpenAIEmbeddings):
        embedding = OpenAIEmbeddings(base_url="http://localhost:9126/vcr/openai")
        with mock.patch("langchain_openai.embeddings.base.tiktoken.encoding_for_model") as mock_encoding_for_model:
            mock_encoding = mock.MagicMock()
            mock_encoding_for_model.return_value = mock_encoding
            mock_encoding.encode.return_value = [0.0] * 1536
            embedding.embed_query("hello world")

    @staticmethod
    def _call_anthropic_chat(Anthropic):
        kwargs = dict(
            model="claude-3-opus-20240229",
            max_tokens=15,
        )

        if "anthropic_api_url" in Anthropic.__fields__:
            kwargs["anthropic_api_url"] = "http://localhost:9126/vcr/anthropic"
        else:
            kwargs["base_url"] = "http://localhost:9126/vcr/anthropic"

        llm = Anthropic(**kwargs)
        llm.invoke("When do you use 'whom' instead of 'who'?")

    @run_in_subprocess(env_overrides=openai_env_config)
    def test_llmobs_with_openai_enabled(self):
        from langchain_openai import OpenAI

        patch(langchain=True, openai=True)
        LLMObs.enable(ml_app="<ml-app-name>", integrations_enabled=False)
        self._call_openai_llm(OpenAI)
        self._assert_trace_structure_from_writer_call_args(["workflow", "llm"])

    @run_in_subprocess(env_overrides=openai_env_config)
    def test_llmobs_with_openai_enabled_non_ascii_value(self):
        """Regression test to ensure that non-ascii text values for workflow spans are not encoded."""
        from langchain_openai import OpenAI

        patch(langchain=True, openai=True)
        LLMObs.enable(ml_app="<ml-app-name>", integrations_enabled=False)
        llm = OpenAI(base_url="http://localhost:9126/vcr/openai")
        llm.invoke("안녕,\n 지금 몇 시야?")
        langchain_span = self.mock_llmobs_span_writer.enqueue.call_args_list[1][0][0]
        assert langchain_span["meta"]["input"]["value"] == '[{"content": "안녕,\\n 지금 몇 시야?"}]'

    @run_in_subprocess(env_overrides=openai_env_config)
    def test_llmobs_langchain_with_embedding_model_openai_enabled(self):
        patch(openai=True, langchain=True)

        from langchain_openai import OpenAIEmbeddings

        LLMObs.enable(ml_app="<ml-app-name>", integrations_enabled=False)
        self._call_openai_embedding(OpenAIEmbeddings)
        self._assert_trace_structure_from_writer_call_args(["workflow", "embedding"])

    @run_in_subprocess(env_overrides=openai_env_config)
    def test_llmobs_langchain_with_embedding_model_openai_disabled(self):
        patch(langchain=True)

        from langchain_openai import OpenAIEmbeddings

        LLMObs.enable(ml_app="<ml-app-name>", integrations_enabled=False)
        self._call_openai_embedding(OpenAIEmbeddings)
        self._assert_trace_structure_from_writer_call_args(["embedding"])

    @run_in_subprocess(env_overrides=openai_env_config)
    def test_llmobs_with_openai_disabled(self):
        from langchain_openai import OpenAI

        patch(langchain=True)

        LLMObs.enable(ml_app="<ml-app-name>", integrations_enabled=False)
        self._call_openai_llm(OpenAI)
        self._assert_trace_structure_from_writer_call_args(["llm"])

    @run_in_subprocess(env_overrides=anthropic_env_config)
    def test_llmobs_with_anthropic_enabled(self):
        from langchain_anthropic import ChatAnthropic

        patch(langchain=True, anthropic=True)

        LLMObs.enable(ml_app="<ml-app-name>", integrations_enabled=False)
        self._call_anthropic_chat(ChatAnthropic)
        self._assert_trace_structure_from_writer_call_args(["workflow", "llm"])

    @run_in_subprocess(env_overrides=anthropic_env_config)
    def test_llmobs_with_anthropic_disabled(self):
        from langchain_anthropic import ChatAnthropic

        patch(langchain=True)

        LLMObs.enable(ml_app="<ml-app-name>", integrations_enabled=False)

        self._call_anthropic_chat(ChatAnthropic)
        self._assert_trace_structure_from_writer_call_args(["llm"])
