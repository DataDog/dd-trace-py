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
from ddtrace.llmobs._utils import _get_llmobs_data_metastruct
from ddtrace.llmobs._utils import get_llmobs_input_messages
from ddtrace.llmobs._utils import get_llmobs_input_prompt
from ddtrace.llmobs._utils import get_llmobs_input_value
from ddtrace.llmobs._utils import get_llmobs_metrics
from ddtrace.llmobs._utils import get_llmobs_output_value
from ddtrace.llmobs._utils import get_llmobs_parent_id
from ddtrace.llmobs._utils import get_llmobs_span_kind
from ddtrace.llmobs._utils import get_llmobs_span_name
from ddtrace.trace import Span
from tests.contrib.langchain.utils import mock_langchain_chat_generate_response
from tests.contrib.langchain.utils import mock_langchain_llm_generate_response
from tests.llmobs._utils import assert_llmobs_span_data
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


COMMON_TAGS = {
    "ml_app": "langchain_test",
    "service": "tests.contrib.langchain",
    "integration": "langchain",
}


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


def _llm_span_kwargs(span, input_role=None, mock_io=False, mock_token_metrics=False, metadata=None):
    """Build kwargs to splat into ``assert_llmobs_span_data`` for a langchain LLM span.

    Mirrors the structural shape produced by the legacy
    ``_expected_langchain_llmobs_llm_span`` helper. The prompt block (when present) is
    asserted separately by callers against ``meta.input.prompt`` on the metastruct.
    """
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

    return dict(
        span_kind="llm",
        model_name=span.get_tag("langchain.request.model"),
        model_provider=provider,
        input_messages=input_messages if not mock_io else mock.ANY,
        output_messages=output_messages if not mock_io else mock.ANY,
        metadata=metadata,
        metrics=metrics,
        tags=COMMON_TAGS,
    )


def _chain_span_kwargs(input_value=None, output_value=None, metadata=None):
    """Build kwargs to splat into ``assert_llmobs_span_data`` for a langchain chain (workflow) span."""
    metadata = metadata if metadata else mock.ANY
    return dict(
        span_kind="workflow",
        input_value=input_value if input_value is not None else mock.ANY,
        output_value=output_value if output_value is not None else mock.ANY,
        metadata=metadata,
        tags=COMMON_TAGS,
    )


def _has_prompt_tracking_tag(metastruct):
    """Return True if the prompt-tracking tag is present (handles dict-shape and list-shape tags)."""
    tags = metastruct.get("tags") or {}
    if isinstance(tags, dict):
        return tags.get("prompt_tracking_instrumentation_method") == "auto"
    return any(t == "prompt_tracking_instrumentation_method:auto" for t in tags)


def _llmobs_spans(test_spans):
    """Collect all spans from ``pop_traces()`` that have an LLMObsSpanData metastruct, sorted by start_ns."""
    spans = [s for trace in test_spans.pop_traces() for s in trace]
    spans = [s for s in spans if _get_llmobs_data_metastruct(s)]
    spans.sort(key=lambda s: s.start_ns)
    return spans


class TestLangChainLLMObs:
    def test_llmobs_openai_llm(self, langchain_openai, langchain_llmobs, test_spans, openai_url):
        llm = langchain_openai.OpenAI(base_url=openai_url, max_tokens=256)
        llm.invoke("Can you explain what Descartes meant by 'I think, therefore I am'?")

        spans = _llmobs_spans(test_spans)
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            **_llm_span_kwargs(spans[0], mock_token_metrics=True, metadata={"max_tokens": 256, "temperature": 0.7}),
        )

    @mock.patch("langchain_core.language_models.llms.BaseLLM._generate_helper")
    def test_llmobs_openai_llm_proxy(self, mock_generate, langchain_openai, langchain_llmobs, test_spans):
        mock_generate.return_value = mock_langchain_llm_generate_response
        llm = langchain_openai.OpenAI(base_url="http://localhost:4000", model="gpt-3.5-turbo")
        llm.invoke("What is the capital of France?")

        spans = _llmobs_spans(test_spans)
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            **_chain_span_kwargs(
                input_value=json.dumps([{"content": "What is the capital of France?"}], sort_keys=True)
            ),
        )

        # span created from request with non-proxy URL should result in an LLM span
        llm = langchain_openai.OpenAI(base_url="http://localhost:8000", model="gpt-3.5-turbo")
        llm.invoke("What is the capital of France?")
        spans = _llmobs_spans(test_spans)
        assert len(spans) == 1
        assert get_llmobs_span_kind(spans[0]) == "llm"

    def test_llmobs_openai_chat_model(self, langchain_core, langchain_openai, langchain_llmobs, test_spans, openai_url):
        chat_model = langchain_openai.ChatOpenAI(temperature=0, max_tokens=256, base_url=openai_url)
        chat_model.invoke([langchain_core.messages.HumanMessage(content="When do you use 'who' instead of 'whom'?")])

        spans = _llmobs_spans(test_spans)
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            **_llm_span_kwargs(
                spans[0], input_role="user", mock_token_metrics=True, metadata={"temperature": 0.0, "max_tokens": 256}
            ),
        )

    def test_llmobs_openai_chat_model_no_usage(
        self, langchain_core, langchain_openai, langchain_llmobs, test_spans, openai_url
    ):
        if parse_version(importlib.metadata.version("langchain_openai")) < (0, 2, 0):
            pytest.skip("langchain-openai <0.2.0 does not support stream_usage=False")
        chat_model = langchain_openai.ChatOpenAI(temperature=0, max_tokens=256, base_url=openai_url, stream_usage=False)

        response = chat_model.stream(
            [langchain_core.messages.HumanMessage(content="When do you use 'who' instead of 'whom'?")]
        )
        for _ in response:
            pass

        spans = _llmobs_spans(test_spans)
        assert len(spans) == 1
        metrics = get_llmobs_metrics(spans[0]) or {}
        assert metrics.get("input_tokens") is None
        assert metrics.get("output_tokens") is None
        assert metrics.get("total_tokens") is None

    @mock.patch("langchain_core.language_models.chat_models.BaseChatModel._generate_with_cache")
    def test_llmobs_openai_chat_model_proxy(
        self, mock_generate, langchain_core, langchain_openai, langchain_llmobs, test_spans
    ):
        mock_generate.return_value = mock_langchain_chat_generate_response
        chat_model = langchain_openai.ChatOpenAI(temperature=0, max_tokens=256, base_url="http://localhost:4000")
        chat_model.invoke([langchain_core.messages.HumanMessage(content="What is the capital of France?")])

        spans = _llmobs_spans(test_spans)
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            **_chain_span_kwargs(
                input_value=json.dumps([{"content": "What is the capital of France?", "role": "user"}], sort_keys=True),
                metadata={"temperature": 0.0, "max_tokens": 256},
            ),
        )

        # span created from request with non-proxy URL should result in an LLM span
        chat_model = langchain_openai.ChatOpenAI(temperature=0, max_tokens=256, base_url="http://localhost:8000")
        chat_model.invoke([langchain_core.messages.HumanMessage(content="What is the capital of France?")])
        spans = _llmobs_spans(test_spans)
        assert len(spans) == 1
        assert get_llmobs_span_kind(spans[0]) == "llm"

    def test_llmobs_string_prompt_template_invoke(
        self, langchain_core, langchain_openai, openai_url, langchain_llmobs, test_spans
    ):
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

        spans = _llmobs_spans(test_spans)
        assert len(spans) == 2
        actual_prompt = get_llmobs_input_prompt(spans[1])
        assert actual_prompt["id"] == "test_langchain_llmobs.prompt_template"
        assert actual_prompt["template"] == template_string
        assert actual_prompt["variables"] == variable_dict
        assert "tags" in actual_prompt
        assert actual_prompt["tags"] == {"test_type": "basic_invoke", "author": "test_suite"}
        assert _has_prompt_tracking_tag(_get_llmobs_data_metastruct(spans[1]))

    def test_llmobs_string_prompt_template_direct_invoke(
        self, langchain_core, langchain_openai, openai_url, langchain_llmobs, test_spans
    ):
        """Test StringPromptTemplate (PromptTemplate) with variable name detection using direct invoke (no chains)."""
        template_string = "Good {time_of_day}, {name}! How are you doing today?"
        variable_dict = {"name": "Alice", "time_of_day": "morning"}
        greeting_template = langchain_core.prompts.PromptTemplate(
            input_variables=list(variable_dict.keys()),
            template=template_string,
            metadata={
                "test_type": "direct_invoke",
                "interaction": "greeting",
                "not_string_1": True,
                "not_string_2": 10,
            },
        )
        llm = langchain_openai.OpenAI(base_url=openai_url)

        # Direct invoke on template first, then pass to LLM (no chain)
        prompt_value = greeting_template.invoke(variable_dict)
        llm.invoke(prompt_value)

        spans = _llmobs_spans(test_spans)
        assert len(spans) == 1  # Only LLM span, prompt template invoke doesn't create LLMObs event by itself

        actual_prompt = get_llmobs_input_prompt(spans[0])
        assert actual_prompt["id"] == "test_langchain_llmobs.greeting_template"
        assert actual_prompt["template"] == template_string
        assert actual_prompt["variables"] == variable_dict
        assert "tags" in actual_prompt
        assert actual_prompt["tags"] == {"test_type": "direct_invoke", "interaction": "greeting"}
        assert _has_prompt_tracking_tag(_get_llmobs_data_metastruct(spans[0]))

    def test_llmobs_string_prompt_template_invoke_chat_model(
        self, langchain_core, langchain_openai, openai_url, langchain_llmobs, test_spans
    ):
        template_string = "You are a helpful assistant. Please answer this question: {question}"
        variable_dict = {"question": "What is machine learning?"}
        prompt_template = langchain_core.prompts.PromptTemplate(
            input_variables=list(variable_dict.keys()), template=template_string
        )
        chat_model = langchain_openai.ChatOpenAI(base_url=openai_url)
        chain = prompt_template | chat_model
        chain.invoke(variable_dict)

        spans = _llmobs_spans(test_spans)
        assert len(spans) == 2
        actual_prompt = get_llmobs_input_prompt(spans[1])
        assert actual_prompt["id"] == "test_langchain_llmobs.prompt_template"
        assert actual_prompt["template"] == template_string
        assert actual_prompt["variables"] == variable_dict

    def test_llmobs_string_prompt_template_single_variable_string_input(
        self, langchain_core, langchain_openai, openai_url, langchain_llmobs, test_spans
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

        spans = _llmobs_spans(test_spans)
        assert len(spans) == 2
        actual_prompt = get_llmobs_input_prompt(spans[1])
        assert actual_prompt["id"] == "test_langchain_llmobs.single_variable_template"
        assert actual_prompt["template"] == template_string
        # Should convert string input to dict format with single variable
        assert actual_prompt["variables"] == {"topic": "time travel"}

    def test_llmobs_multi_message_prompt_template_sync_chain(
        self, langchain_core, langchain_openai, openai_url, langchain_llmobs, test_spans
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

        spans = _llmobs_spans(test_spans)
        assert len(spans) == 2

        # Verify the prompt structure in the LLM span
        actual_prompt = get_llmobs_input_prompt(spans[1])
        assert actual_prompt["id"] == "test_langchain_llmobs.multi_message_template"
        assert actual_prompt["variables"] == variable_dict
        assert actual_prompt.get("template") is None
        assert actual_prompt["chat_template"] == PROMPT_TEMPLATE_EXPECTED_CHAT_TEMPLATE
        # Check that metadata from the multi-message prompt template is preserved
        assert "tags" in actual_prompt
        assert actual_prompt["tags"] == {k: v for k, v in test_metadata.items() if isinstance(v, str)}

    def test_llmobs_multi_message_prompt_template_sync_direct_invoke(
        self, langchain_core, langchain_openai, openai_url, langchain_llmobs, test_spans
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

        spans = _llmobs_spans(test_spans)
        assert len(spans) == 1  # Only LLM span for direct invoke

        # Verify the prompt structure
        actual_prompt = get_llmobs_input_prompt(spans[0])
        assert actual_prompt["id"] == "test_langchain_llmobs.multi_message_template"
        assert actual_prompt["variables"] == variable_dict
        assert actual_prompt.get("template") is None
        assert actual_prompt["chat_template"] == PROMPT_TEMPLATE_EXPECTED_CHAT_TEMPLATE

    @pytest.mark.asyncio
    async def test_llmobs_multi_message_prompt_template_async_direct_invoke(
        self, langchain_core, langchain_openai, openai_url, langchain_llmobs, test_spans
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

        spans = _llmobs_spans(test_spans)
        assert len(spans) == 1  # Only LLM span for direct invoke

        # Verify the prompt structure
        actual_prompt = get_llmobs_input_prompt(spans[0])
        assert actual_prompt["id"] == "test_langchain_llmobs.multi_message_template"
        assert actual_prompt["variables"] == variable_dict
        assert actual_prompt.get("template") is None
        assert actual_prompt["chat_template"] == PROMPT_TEMPLATE_EXPECTED_CHAT_TEMPLATE

    def test_llmobs_chain(self, langchain_core, langchain_openai, openai_url, langchain_llmobs, test_spans):
        prompt = langchain_core.prompts.ChatPromptTemplate.from_messages(
            [("system", "You are world class technical documentation writer."), ("user", "{input}")]
        )
        chain = prompt | langchain_openai.OpenAI(base_url=openai_url, max_tokens=256)
        chain.invoke({"input": "Can you explain what an LLM chain is?"})

        spans = _llmobs_spans(test_spans)
        assert len(spans) == 2
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            **_chain_span_kwargs(
                input_value=json.dumps([{"input": "Can you explain what an LLM chain is?"}], sort_keys=True),
            ),
        )
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[1]),
            **_llm_span_kwargs(spans[1], mock_token_metrics=True, metadata={"max_tokens": 256, "temperature": 0.7}),
        )
        expected_prompt = {
            "id": "test_langchain_llmobs.prompt",
            "ml_app": "langchain_test",
            "chat_template": [
                {"content": "You are world class technical documentation writer.", "role": "system"},
                {"content": "{input}", "role": "user"},
            ],
            "variables": {"input": "Can you explain what an LLM chain is?"},
            "_dd_context_variable_keys": ["context"],
            "_dd_query_variable_keys": ["question"],
        }
        assert get_llmobs_input_prompt(spans[1]) == expected_prompt

    def test_llmobs_chain_nested(self, langchain_core, langchain_openai, openai_url, langchain_llmobs, test_spans):
        prompt1 = langchain_core.prompts.ChatPromptTemplate.from_template("what is the city {person} is from?")
        prompt2 = langchain_core.prompts.ChatPromptTemplate.from_template(
            "what country is the city {city} in? respond in {language}"
        )
        model = langchain_openai.OpenAI(base_url=openai_url)
        chain1 = prompt1 | model | langchain_core.output_parsers.StrOutputParser()
        chain2 = prompt2 | model | langchain_core.output_parsers.StrOutputParser()
        complete_chain = {"city": chain1, "language": itemgetter("language")} | chain2

        complete_chain.invoke({"person": "Spongebob Squarepants", "language": "Spanish"})

        # Use the APM trace (parent-first ordering) directly; the legacy test indexed by
        # ``trace[i]`` here, and that maps 1:1 to the LLMObs spans on the same Spans.
        trace = test_spans.pop_traces()[0]
        spans = [s for s in trace if _get_llmobs_data_metastruct(s)]
        assert len(spans) == 5

        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            **_chain_span_kwargs(
                input_value=json.dumps([{"person": "Spongebob Squarepants", "language": "Spanish"}], sort_keys=True),
                output_value=mock.ANY,
            ),
        )
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[1]),
            **_chain_span_kwargs(
                input_value=json.dumps([{"person": "Spongebob Squarepants", "language": "Spanish"}], sort_keys=True),
                output_value=mock.ANY,
            ),
        )
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[2]),
            span_kind="task",
            input_value=json.dumps({"person": "Spongebob Squarepants", "language": "Spanish"}, sort_keys=True),
            output_value="Spanish",
            tags=COMMON_TAGS,
        )
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[3]), **_llm_span_kwargs(spans[3], mock_token_metrics=True)
        )
        expected_prompt_3 = {
            "id": "langchain.unknown_prompt_template",
            "ml_app": "langchain_test",
            "chat_template": [{"content": "what is the city {person} is from?", "role": "user"}],
            "variables": {"person": "Spongebob Squarepants", "language": "Spanish"},
            "_dd_context_variable_keys": ["context"],
            "_dd_query_variable_keys": ["question"],
        }
        assert get_llmobs_input_prompt(spans[3]) == expected_prompt_3

        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[4]), **_llm_span_kwargs(spans[4], mock_token_metrics=True)
        )
        expected_prompt_4 = {
            "id": "test_langchain_llmobs.prompt2",
            "ml_app": "langchain_test",
            "chat_template": [{"content": "what country is the city {city} in? respond in {language}", "role": "user"}],
            "variables": {"city": mock.ANY, "language": "Spanish"},
            "_dd_context_variable_keys": ["context"],
            "_dd_query_variable_keys": ["question"],
        }
        assert get_llmobs_input_prompt(spans[4]) == expected_prompt_4

    @pytest.mark.skipif(sys.version_info >= (3, 11), reason="Python <3.11 required")
    def test_llmobs_chain_batch(self, langchain_core, langchain_openai, langchain_llmobs, test_spans, openai_url):
        prompt = langchain_core.prompts.ChatPromptTemplate.from_template("Tell me a short joke about {topic}")
        output_parser = langchain_core.output_parsers.StrOutputParser()
        model = langchain_openai.ChatOpenAI(base_url=openai_url)
        chain = {"topic": langchain_core.runnables.RunnablePassthrough()} | prompt | model | output_parser

        chain.batch(inputs=["chickens", "pigs"])

        spans = _llmobs_spans(test_spans)
        assert len(spans) == 3
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            **_chain_span_kwargs(input_value=json.dumps(["chickens", "pigs"], sort_keys=True), output_value=mock.ANY),
        )

        # The two LLM spans (one per batch item) can come back in either order; check by
        # matching the variables.topic in each span's prompt block.
        for child in spans[1:3]:
            assert_llmobs_span_data(
                _get_llmobs_data_metastruct(child),
                **_llm_span_kwargs(child, input_role="user", mock_token_metrics=True),
            )
            prompt_block = get_llmobs_input_prompt(child)
            assert prompt_block["id"] == "langchain.unknown_prompt_template"
            assert prompt_block["ml_app"] == "langchain_test"
            assert prompt_block["chat_template"] == [{"content": "Tell me a short joke about {topic}", "role": "user"}]
            assert prompt_block["variables"]["topic"] in ("chickens", "pigs")

    def test_llmobs_chain_schema_io(self, langchain_core, langchain_openai, openai_url, langchain_llmobs, test_spans):
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

        spans = _llmobs_spans(test_spans)
        assert len(spans) == 2
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            **_chain_span_kwargs(
                input_value=json.dumps(
                    [
                        {
                            "ability": "world capitals",
                            "history": [["user", "Can you be my science teacher instead?"], ["assistant", "Yes"]],
                            "input": "What's the powerhouse of the cell?",
                        }
                    ],
                    sort_keys=True,
                ),
                output_value=json.dumps(["assistant", "Mitochondria"], sort_keys=True),
            ),
        )
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[1]),
            **_llm_span_kwargs(spans[1], mock_io=True, mock_token_metrics=True),
        )

    def test_llmobs_anthropic_chat_model(self, langchain_anthropic, langchain_llmobs, test_spans, anthropic_url):
        kwargs = dict(
            temperature=0,
            max_tokens=15,
            model_name="claude-sonnet-4-5-20250929",
        )

        if "anthropic_api_url" in langchain_anthropic.ChatAnthropic.__fields__:
            kwargs["anthropic_api_url"] = anthropic_url
        else:
            kwargs["base_url"] = anthropic_url

        chat = langchain_anthropic.ChatAnthropic(**kwargs)
        chat.invoke("When do you use 'whom' instead of 'who'?")

        spans = _llmobs_spans(test_spans)
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            **_llm_span_kwargs(
                spans[0], input_role="user", mock_token_metrics=True, metadata={"temperature": 0, "max_tokens": 15}
            ),
        )

    def test_llmobs_google_genai_chat_model(self, langchain_google_genai, langchain_llmobs, test_spans):
        if langchain_google_genai is None:
            pytest.skip("langchain-google-genai not installed")

        try:
            from google.genai import types
        except ImportError:
            pytest.skip("google-genai SDK not installed (langchain-google-genai 2.x uses legacy SDK)")

        mock_response = types.GenerateContentResponse(
            candidates=[
                types.Candidate(
                    content=types.Content(
                        role="model",
                        parts=[types.Part.from_text(text="You use 'whom' as the object of a verb or preposition.")],
                    )
                )
            ],
            usage_metadata=types.GenerateContentResponseUsageMetadata(
                prompt_token_count=10, candidates_token_count=12, total_token_count=22
            ),
        )

        with mock.patch("google.genai.models.Models._generate_content", return_value=mock_response):
            chat = langchain_google_genai.ChatGoogleGenerativeAI(
                model="gemini-2.5-flash",
                temperature=0,
                max_output_tokens=15,
            )
            chat.invoke("When do you use 'whom' instead of 'who'?")

        spans = _llmobs_spans(test_spans)
        assert len(spans) == 1
        span = spans[0]
        # Verify the fix: provider should be "google-generativeai" (not "chat"),
        # and model name should be "gemini-2.5-flash" (not "models/gemini-2.5-flash").
        assert span.get_tag("langchain.request.provider") == "google-generative-ai"
        assert span.get_tag("langchain.request.model") == "gemini-2.5-flash"
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(span),
            **_llm_span_kwargs(span, input_role="user", mock_token_metrics=True, metadata={"temperature": 0.0}),
        )

    def test_llmobs_embedding_documents(self, langchain_openai, langchain_llmobs, test_spans, openai_url):
        embedding_model = langchain_openai.embeddings.OpenAIEmbeddings(base_url=openai_url)
        embedding_model.embed_documents(["hello world", "goodbye world"])

        spans = _llmobs_spans(test_spans)
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="embedding",
            model_name="text-embedding-ada-002",
            model_provider="openai",
            input_documents=[{"text": "hello world"}, {"text": "goodbye world"}],
            output_value="[2 embedding(s) returned with size 1536]",
            tags=COMMON_TAGS,
        )

    def test_llmobs_embedding_query(self, langchain_openai, langchain_llmobs, test_spans, openai_url):
        embedding_model = langchain_openai.embeddings.OpenAIEmbeddings(base_url=openai_url)
        embedding_model.embed_query("hello world")

        spans = _llmobs_spans(test_spans)
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="embedding",
            model_name=embedding_model.model,
            model_provider="openai",
            input_documents=[{"text": "hello world"}],
            output_value="[1 embedding(s) returned with size 1536]",
            tags=COMMON_TAGS,
        )

    def test_llmobs_vectorstore_similarity_search(self, langchain_in_memory_vectorstore, langchain_llmobs, test_spans):
        vectorstore = langchain_in_memory_vectorstore
        vectorstore.similarity_search("France", k=1)

        spans = _llmobs_spans(test_spans)
        assert len(spans) == 2
        # spans[0] is the retrieval span (outer); spans[1] is the embedding sub-call.
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="retrieval",
            input_value="France",
            output_documents=[{"text": mock.ANY, "id": mock.ANY, "name": mock.ANY}],
            output_value="[1 document(s) retrieved]",
            tags=COMMON_TAGS,
        )

    def test_llmobs_chat_model_tool_calls(
        self, langchain_core, langchain_openai, langchain_llmobs, test_spans, openai_url
    ):
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

        spans = _llmobs_spans(langchain_llmobs, test_spans)
        assert len(spans) == 1
        span = spans[0]
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(span),
            span_kind="llm",
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
            metrics={"input_tokens": mock.ANY, "output_tokens": mock.ANY, "total_tokens": mock.ANY},
            tags=COMMON_TAGS,
        )

    def test_llmobs_base_tool_invoke(self, langchain_core, langchain_llmobs, test_spans):
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

        spans = _llmobs_spans(langchain_llmobs, test_spans)
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
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
            tags=COMMON_TAGS,
        )

    @pytest.mark.parametrize("consume_stream", [iterate_stream, next_stream])
    def test_llmobs_streamed_chain(
        self, langchain_core, langchain_openai, langchain_llmobs, test_spans, openai_url, consume_stream
    ):
        prompt = langchain_core.prompts.ChatPromptTemplate.from_messages(
            [("system", "You are a world class technical documentation writer."), ("user", "{input}")]
        )
        llm = langchain_openai.ChatOpenAI(model="gpt-4o", base_url=openai_url, temperature=0.7, max_tokens=256)
        parser = langchain_core.output_parsers.StrOutputParser()

        chain = prompt | llm | parser

        consume_stream(chain.stream({"input": "how can langsmith help with testing?"}))

        spans = _llmobs_spans(test_spans)
        assert len(spans) == 2
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            **_chain_span_kwargs(
                input_value=json.dumps({"input": "how can langsmith help with testing?"}, sort_keys=True),
                output_value=mock.ANY,
            ),
        )
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[1]),
            span_kind="llm",
            model_name=spans[1].get_tag("langchain.request.model"),
            model_provider=spans[1].get_tag("langchain.request.provider"),
            input_messages=[
                {"content": "You are a world class technical documentation writer.", "role": "SystemMessage"},
                {"content": "how can langsmith help with testing?", "role": "HumanMessage"},
            ],
            output_messages=[{"content": mock.ANY, "role": "assistant"}],
            metadata={"temperature": 0.7, "max_tokens": 256},
            metrics={},
            tags=COMMON_TAGS,
        )

    @pytest.mark.parametrize("consume_stream", [iterate_stream, next_stream])
    def test_llmobs_streamed_llm(self, langchain_openai, langchain_llmobs, test_spans, openai_url, consume_stream):
        llm = langchain_openai.OpenAI(base_url=openai_url, n=1, seed=1, logprobs=None, logit_bias=None)

        consume_stream(llm.stream("What is 2+2?\n\n"))

        spans = _llmobs_spans(test_spans)
        assert len(spans) == 1
        span = spans[0]
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(span),
            span_kind="llm",
            model_name=span.get_tag("langchain.request.model"),
            model_provider=span.get_tag("langchain.request.provider"),
            input_messages=[
                {"content": "What is 2+2?\n\n"},
            ],
            output_messages=[{"content": "The answer is 4."}],
            metadata={"temperature": 0.7, "max_tokens": 256},
            metrics={},
            tags=COMMON_TAGS,
        )

    def test_llmobs_non_ascii_completion(self, langchain_openai, openai_url, langchain_llmobs, test_spans):
        llm = langchain_openai.OpenAI(base_url=openai_url)
        llm.invoke("안녕,\n 지금 몇 시야?")

        spans = _llmobs_spans(test_spans)
        assert len(spans) == 1
        assert get_llmobs_input_messages(spans[0])[0]["content"] == "안녕,\n 지금 몇 시야?"

    def test_llmobs_runnable_lambda_invoke(self, langchain_core, langchain_llmobs, test_spans):
        def add(inputs: dict) -> int:
            return inputs["a"] + inputs["b"]

        runnable_lambda = langchain_core.runnables.RunnableLambda(add)
        result = runnable_lambda.invoke(dict(a=1, b=2))
        assert result == 3

        spans = _llmobs_spans(langchain_llmobs, test_spans)
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="task",
            input_value=json.dumps({"a": 1, "b": 2}, sort_keys=True),
            output_value="3",
            tags=COMMON_TAGS,
        )

    async def test_llmobs_runnable_lambda_ainvoke(self, langchain_core, langchain_llmobs, test_spans):
        async def add(inputs: dict) -> int:
            return inputs["a"] + inputs["b"]

        runnable_lambda = langchain_core.runnables.RunnableLambda(add)
        result = await runnable_lambda.ainvoke(dict(a=1, b=2))
        assert result == 3

        spans = _llmobs_spans(langchain_llmobs, test_spans)
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="task",
            input_value=json.dumps({"a": 1, "b": 2}, sort_keys=True),
            output_value="3",
            tags=COMMON_TAGS,
        )

    def test_llmobs_runnable_lambda_batch(self, langchain_core, langchain_llmobs, test_spans):
        def add(inputs: dict) -> int:
            return inputs["a"] + inputs["b"]

        runnable_lambda = langchain_core.runnables.RunnableLambda(add)
        result = runnable_lambda.batch([dict(a=1, b=2), dict(a=3, b=4), dict(a=5, b=6)])
        assert result == [3, 7, 11]

        spans = _llmobs_spans(langchain_llmobs, test_spans)
        assert len(spans) == 4

        # parent should be batch span
        assert get_llmobs_parent_id(spans[0]) == "undefined"
        assert get_llmobs_span_name(spans[0]) == "add_batch"
        assert get_llmobs_span_kind(spans[0]) == "task"
        assert get_llmobs_input_value(spans[0]) == json.dumps(
            [{"a": 1, "b": 2}, {"a": 3, "b": 4}, {"a": 5, "b": 6}], sort_keys=True
        )
        assert get_llmobs_output_value(spans[0]) == json.dumps([3, 7, 11], sort_keys=True)

        # assert all children have batch as the parent
        # however, order of children is not guaranteed
        # loosely check that the children have the correct input and output values
        parent_span_id = str(spans[0].span_id)
        for i in range(1, 4):
            assert get_llmobs_parent_id(spans[i]) == parent_span_id
            assert get_llmobs_span_name(spans[i]) == "add"
            assert get_llmobs_span_kind(spans[i]) == "task"

            input_value = json.loads(get_llmobs_input_value(spans[i]))
            output_value = json.loads(get_llmobs_output_value(spans[i]))
            assert "a" in input_value
            assert "b" in input_value
            assert isinstance(output_value, int)

    async def test_llmobs_runnable_lambda_abatch(self, langchain_core, langchain_llmobs, test_spans):
        async def add(inputs: dict) -> int:
            return inputs["a"] + inputs["b"]

        runnable_lambda = langchain_core.runnables.RunnableLambda(add)
        result = await runnable_lambda.abatch([dict(a=1, b=2), dict(a=3, b=4), dict(a=5, b=6)])
        assert result == [3, 7, 11]

        spans = _llmobs_spans(langchain_llmobs, test_spans)
        assert len(spans) == 4

        # parent should be batch span
        assert get_llmobs_parent_id(spans[0]) == "undefined"
        assert get_llmobs_span_name(spans[0]) == "add_batch"
        assert get_llmobs_span_kind(spans[0]) == "task"
        assert get_llmobs_input_value(spans[0]) == json.dumps(
            [{"a": 1, "b": 2}, {"a": 3, "b": 4}, {"a": 5, "b": 6}], sort_keys=True
        )
        assert get_llmobs_output_value(spans[0]) == json.dumps([3, 7, 11], sort_keys=True)

        # assert all children have batch as the parent
        # however, order of children is not guaranteed
        # loosely check that the children have the correct input and output values
        parent_span_id = str(spans[0].span_id)
        for i in range(1, 4):
            assert get_llmobs_parent_id(spans[i]) == parent_span_id
            assert get_llmobs_span_name(spans[i]) == "add"
            assert get_llmobs_span_kind(spans[i]) == "task"

            input_value = json.loads(get_llmobs_input_value(spans[i]))
            output_value = json.loads(get_llmobs_output_value(spans[i]))
            assert "a" in input_value
            assert "b" in input_value
            assert isinstance(output_value, int)

    def test_llmobs_runnable_with_span_links(self, langchain_core, langchain_llmobs, test_spans):
        sequence = langchain_core.runnables.RunnableLambda(lambda x: x + 1, name="add_1") | {
            "mul_2": langchain_core.runnables.RunnableLambda(lambda x: x * 2, name="mul_2"),
            "mul_5": langchain_core.runnables.RunnableLambda(lambda x: x * 5, name="mul_5"),
        }

        result = sequence.invoke(1)

        assert result == {"mul_2": 4, "mul_5": 10}

        spans = _llmobs_spans(test_spans)
        assert len(spans) == 4

        metas = [_get_llmobs_data_metastruct(s) for s in spans]
        span_ids = [str(s.span_id) for s in spans]

        # assert output -> output links for runnable sequence span from last two runnable lambdas
        # the order of the links is not guaranteed
        expected_links = [
            {
                "trace_id": mock.ANY,
                "span_id": span_ids[2],
                "attributes": {"from": "output", "to": "output"},
            },
            {
                "trace_id": mock.ANY,
                "span_id": span_ids[3],
                "attributes": {"from": "output", "to": "output"},
            },
        ]
        assert len(metas[0]["span_links"]) == len(expected_links)
        for expected_link in expected_links:
            assert expected_link in metas[0]["span_links"]

        # assert input -> input links for add_1 span
        assert metas[1]["span_links"] == [
            {
                "trace_id": mock.ANY,
                "span_id": span_ids[0],
                "attributes": {"from": "input", "to": "input"},
            },
        ]

        # assert output -> input links for mul_2 and mul_5 spans
        assert metas[2]["span_links"] == [
            {
                "trace_id": mock.ANY,
                "span_id": span_ids[1],
                "attributes": {"from": "output", "to": "input"},
            },
        ]
        assert metas[3]["span_links"] == [
            {
                "trace_id": mock.ANY,
                "span_id": span_ids[1],
                "attributes": {"from": "output", "to": "input"},
            },
        ]


def test_llmobs_set_tags_with_none_response(langchain_core):
    integration = langchain_core._datadog_integration
    integration._llmobs_set_tags(
        span=Span("test_span", service="test_service"),
        args=[],
        kwargs={},
        response=None,
        operation="chat",
    )


# TODO(meta-struct migration): The TestTraceStructureWithLLMIntegrations class
# below verifies cross-integration trace structure (langchain + openai/anthropic
# patched together) by patching ``ddtrace.llmobs._llmobs.LLMObsSpanWriter`` and
# inspecting ``enqueue.call_args_list`` directly inside subprocesses spawned by
# ``run_in_subprocess``. The subprocesses don't share fixture state with the
# parent pytest process, so the meta_struct env-var/test_spans-override approach
# can't be used here without re-architecting how each subprocess test is set up.
# Leaving on the wire-event pattern for the cleanup PR to address.
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

    azure_openai_env_config = dict(
        OPENAI_API_VERSION="2024-12-01-preview",
        AZURE_OPENAI_API_KEY=os.getenv("AZURE_OPENAI_API_KEY", "testing"),
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

            assert call_args["meta"]["span"]["kind"] == span_kind, (
                f"Span kind is {call_args['meta']['span']['kind']} but expected {span_kind}"
            )
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
    def _call_azure_openai_chat(AzureChatOpenAI):
        llm = AzureChatOpenAI(azure_endpoint="http://localhost:9126/vcr/azure_openai", deployment_name="gpt-4.1-mini")
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
            model="claude-sonnet-4-5-20250929",
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

    @run_in_subprocess(env_overrides=azure_openai_env_config)
    def test_llmobs_with_openai_enabled_azure(self):
        from langchain_openai import AzureChatOpenAI

        patch(langchain=True, openai=True)
        LLMObs.enable(ml_app="<ml-app-name>", integrations_enabled=False)
        self._call_azure_openai_chat(AzureChatOpenAI)
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

    @run_in_subprocess(env_overrides=azure_openai_env_config)
    def test_llmobs_with_openai_disabled_azure(self):
        from langchain_openai import AzureChatOpenAI

        patch(langchain=True)

        LLMObs.enable(ml_app="<ml-app-name>", integrations_enabled=False)
        self._call_azure_openai_chat(AzureChatOpenAI)
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


def test_shadow_tags_chat_when_llmobs_disabled(tracer):
    """Verify shadow tags are set on LangChain spans when LLMObs is disabled."""
    from unittest.mock import MagicMock

    from ddtrace.llmobs._integrations.langchain import LangChainIntegration

    integration = LangChainIntegration(MagicMock())

    response = MagicMock()
    response.llm_output = {"token_usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}}
    response.generations = [[MagicMock()]]

    with tracer.trace("langchain.request") as span:
        span._set_attribute("langchain.request.model", "gpt-4o-mini")
        span._set_attribute("langchain.request.provider", "openai")
        integration._set_apm_shadow_tags(span, [], {}, response=response, operation="chat")

    assert span.get_tag("_dd.llmobs.span_kind") == "llm"
    assert span.get_tag("_dd.llmobs.model_name") == "gpt-4o-mini"
    assert span.get_tag("_dd.llmobs.model_provider") == "openai"
    assert span.get_metric("_dd.llmobs.enabled") == 0
    assert span.get_metric("_dd.llmobs.input_tokens") == 5
    assert span.get_metric("_dd.llmobs.output_tokens") == 3
    assert span.get_metric("_dd.llmobs.total_tokens") == 8
