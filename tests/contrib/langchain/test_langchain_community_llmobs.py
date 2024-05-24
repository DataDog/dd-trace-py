import json
from operator import itemgetter
import sys

import langchain
import mock
import pytest

from ddtrace.contrib.langchain.patch import SHOULD_PATCH_LANGCHAIN_COMMUNITY
from ddtrace.llmobs import LLMObs
from ddtrace.llmobs._utils import _expected_llmobs_llm_span_event
from ddtrace.llmobs._utils import _expected_llmobs_non_llm_span_event
from tests.contrib.langchain.conftest import get_request_vcr
from tests.utils import flaky


pytestmark = pytest.mark.skipif(
    not SHOULD_USE_LANGCHAIN_COMMUNITY or sys.version_info < (3, 10),
    reason="This module only tests langchain_community and Python 3.10+",
)


@pytest.fixture(scope="session")
def request_vcr():
    yield get_request_vcr(subdirectory_name="langchain")


class TestLLMObsLangchain:
    @staticmethod
    def _expected_llmobs_chain_calls(trace, expected_spans_data: list):
        expected_llmobs_writer_calls = [mock.call.start()]

        for idx, span in enumerate(trace):
            kind, kwargs = expected_spans_data[idx]
            expected_span_event = None
            if kind == "chain":
                expected_span_event = TestLLMObsLangchain._expected_llmobs_chain_call(span, **kwargs)
            else:
                expected_span_event = TestLLMObsLangchain._expected_llmobs_llm_call(span, **kwargs)

            expected_llmobs_writer_calls += [mock.call.enqueue(expected_span_event)]

        return expected_llmobs_writer_calls

    @staticmethod
    def _expected_llmobs_chain_call(span, input_parameters=None, input_value=None, output_value=None):
        return _expected_llmobs_non_llm_span_event(
            span,
            span_kind="workflow",
            parameters=input_parameters,
            input_value=input_value,
            output_value=output_value,
            tags={
                "ml_app": "langchain_community_test",
            },
            integration="langchain",
        )

    @staticmethod
    def _expected_llmobs_llm_call(span, provider="openai", input_roles=[None], output_role=None):
        input_meta = [{"content": mock.ANY} for _ in input_roles]
        for idx, role in enumerate(input_roles):
            if role is not None:
                input_meta[idx]["role"] = role

        output_meta = {"content": mock.ANY}
        if output_role is not None:
            output_meta["role"] = output_role

        temperature_key = "temperature"
        if provider == "huggingface_hub":
            max_tokens_key = "model_kwargs.max_tokens"
            temperature_key = "model_kwargs.temperature"
        elif provider == "ai21":
            max_tokens_key = "maxTokens"
        else:
            max_tokens_key = "max_tokens"

        metadata = {}
        temperature = span.get_tag(f"langchain.request.{provider}.parameters.{temperature_key}")
        max_tokens = span.get_tag(f"langchain.request.{provider}.parameters.{max_tokens_key}")
        if temperature is not None:
            metadata["temperature"] = float(temperature)
        if max_tokens is not None:
            metadata["max_tokens"] = int(max_tokens)

        return _expected_llmobs_llm_span_event(
            span,
            model_name=span.get_tag("langchain.request.model"),
            model_provider=span.get_tag("langchain.request.provider"),
            input_messages=input_meta,
            output_messages=[output_meta],
            metadata=metadata,
            token_metrics={},
            tags={
                "ml_app": "langchain_community_test",
            },
            integration="langchain",
        )

    @classmethod
    def _test_llmobs_llm_invoke(
        cls,
        provider,
        generate_trace,
        request_vcr,
        mock_llmobs_span_writer,
        mock_tracer,
        cassette_name,
        input_roles=[None],
        output_role=None,
    ):
        LLMObs.enable(ml_app="langchain_community_test", integrations_enabled=False, _tracer=mock_tracer)

        with request_vcr.use_cassette(cassette_name):
            generate_trace("Can you explain what an LLM chain is?")
        span = mock_tracer.pop_traces()[0][0]

        expected_llmons_writer_calls = [
            mock.call.start(),
            mock.call.enqueue(
                cls._expected_llmobs_llm_call(
                    span,
                    provider=provider,
                    input_roles=input_roles,
                    output_role=output_role,
                )
            ),
        ]

        assert mock_llmobs_span_writer.enqueue.call_count == 1
        mock_llmobs_span_writer.assert_has_calls(expected_llmons_writer_calls)
        LLMObs.disable()

    @classmethod
    def _test_llmobs_chain_invoke(
        cls,
        generate_trace,
        request_vcr,
        mock_llmobs_span_writer,
        mock_tracer,
        cassette_name,
        expected_spans_data=[("llm", {"provider": "openai", "input_roles": [None], "output_role": None})],
    ):
        LLMObs.enable(ml_app="langchain_community_test", integrations_enabled=False, _tracer=mock_tracer)

        with request_vcr.use_cassette(cassette_name):
            generate_trace("Can you explain what an LLM chain is?")
        trace = mock_tracer.pop_traces()[0]

        expected_llmobs_writer_calls = cls._expected_llmobs_chain_calls(
            trace=trace, expected_spans_data=expected_spans_data
        )
        assert mock_llmobs_span_writer.enqueue.call_count == len(expected_spans_data)
        mock_llmobs_span_writer.assert_has_calls(expected_llmobs_writer_calls)
        LLMObs.disable()

    @flaky(1735812000)
    def test_llmobs_openai_llm(self, langchain_openai, mock_llmobs_span_writer, mock_tracer, request_vcr):
        llm = langchain_openai.OpenAI()

        self._test_llmobs_llm_invoke(
            generate_trace=llm.invoke,
            request_vcr=request_vcr,
            mock_llmobs_span_writer=mock_llmobs_span_writer,
            mock_tracer=mock_tracer,
            cassette_name="openai_completion_sync.yaml",
            provider="openai",
        )

    def test_llmobs_cohere_llm(self, langchain_community, mock_llmobs_span_writer, mock_tracer, request_vcr):
        llm = langchain_community.llms.Cohere(model="cohere.command-light-text-v14")

        self._test_llmobs_llm_invoke(
            generate_trace=llm.invoke,
            request_vcr=request_vcr,
            mock_llmobs_span_writer=mock_llmobs_span_writer,
            mock_tracer=mock_tracer,
            cassette_name="cohere_completion_sync.yaml",
            provider="cohere",
        )

    def test_llmobs_ai21_llm(self, langchain_community, mock_llmobs_span_writer, mock_tracer, request_vcr):
        llm = langchain_community.llms.AI21()

        self._test_llmobs_llm_invoke(
            generate_trace=llm.invoke,
            request_vcr=request_vcr,
            mock_llmobs_span_writer=mock_llmobs_span_writer,
            mock_tracer=mock_tracer,
            cassette_name="ai21_completion_sync.yaml",
            provider="ai21",
        )

    @flaky(1735812000)
    def test_llmobs_openai_chat_model(self, langchain_openai, mock_llmobs_span_writer, mock_tracer, request_vcr):
        chat = langchain_openai.ChatOpenAI(temperature=0, max_tokens=256)

        self._test_llmobs_llm_invoke(
            generate_trace=lambda prompt: chat.invoke([langchain.schema.HumanMessage(content=prompt)]),
            request_vcr=request_vcr,
            mock_llmobs_span_writer=mock_llmobs_span_writer,
            mock_tracer=mock_tracer,
            cassette_name="openai_chat_completion_sync_call.yaml",
            provider="openai",
            input_roles=["user"],
            output_role="assistant",
        )

    @flaky(1735812000)
    def test_llmobs_openai_chat_model_custom_role(
        self, langchain_openai, mock_llmobs_span_writer, mock_tracer, request_vcr
    ):
        chat = langchain_openai.ChatOpenAI(temperature=0, max_tokens=256)

        self._test_llmobs_llm_invoke(
            generate_trace=lambda prompt: chat.invoke([langchain.schema.ChatMessage(content=prompt, role="custom")]),
            request_vcr=request_vcr,
            mock_llmobs_span_writer=mock_llmobs_span_writer,
            mock_tracer=mock_tracer,
            cassette_name="openai_chat_completion_sync_call.yaml",
            provider="openai",
            input_roles=["custom"],
            output_role="assistant",
        )

    @flaky(1735812000)
    def test_llmobs_chain(self, langchain_core, langchain_openai, mock_llmobs_span_writer, mock_tracer, request_vcr):
        prompt = langchain_core.prompts.ChatPromptTemplate.from_messages(
            [("system", "You are world class technical documentation writer."), ("user", "{input}")]
        )
        llm = langchain_openai.OpenAI()

        chain = prompt | llm

        expected_output = (
            "\nSystem: Langsmith can help with testing in several ways. "
            "First, it can generate automated tests based on your technical documentation, "
            "ensuring that your code matches the documented specifications. "
            "This can save you time and effort in testing your code manually. "
            "Additionally, Langsmith can also analyze your technical documentation for completeness and accuracy, "
            "helping you identify any potential gaps or errors before testing begins. "
            "Finally, Langsmith can assist with creating test cases and scenarios based on your documentation, "
            "making the testing process more efficient and effective."
        )

        self._test_llmobs_chain_invoke(
            generate_trace=lambda prompt: chain.invoke({"input": prompt}),
            request_vcr=request_vcr,
            mock_llmobs_span_writer=mock_llmobs_span_writer,
            mock_tracer=mock_tracer,
            cassette_name="lcel_openai_chain_call.yaml",
            expected_spans_data=[
                (
                    "chain",
                    {
                        "input_value": json.dumps([{"input": "Can you explain what an LLM chain is?"}]),
                        "output_value": expected_output,
                    },
                ),
                ("llm", {"provider": "openai", "input_roles": [None], "output_role": None}),
            ],
        )

    def test_llmobs_chain_nested(
        self, langchain_core, langchain_openai, mock_llmobs_span_writer, mock_tracer, request_vcr
    ):
        prompt1 = langchain_core.prompts.ChatPromptTemplate.from_template("what is the city {person} is from?")
        prompt2 = langchain_core.prompts.ChatPromptTemplate.from_template(
            "what country is the city {city} in? respond in {language}"
        )

        model = langchain_openai.ChatOpenAI()

        chain1 = prompt1 | model | langchain_core.output_parsers.StrOutputParser()
        chain2 = prompt2 | model | langchain_core.output_parsers.StrOutputParser()

        complete_chain = {"city": chain1, "language": itemgetter("language")} | chain2

        self._test_llmobs_chain_invoke(
            generate_trace=lambda inputs: complete_chain.invoke(
                {"person": "Spongebob Squarepants", "language": "Spanish"}
            ),
            request_vcr=request_vcr,
            mock_llmobs_span_writer=mock_llmobs_span_writer,
            mock_tracer=mock_tracer,
            cassette_name="lcel_openai_chain_nested.yaml",
            expected_spans_data=[
                (
                    "chain",
                    {
                        "input_value": json.dumps([{"person": "Spongebob Squarepants", "language": "Spanish"}]),
                        "output_value": mock.ANY,
                    },
                ),
                (
                    "chain",
                    {
                        "input_value": json.dumps([{"person": "Spongebob Squarepants", "language": "Spanish"}]),
                        "output_value": mock.ANY,
                    },
                ),
                ("llm", {"provider": "openai", "input_roles": ["user"], "output_role": "assistant"}),
                ("llm", {"provider": "openai", "input_roles": ["user"], "output_role": "assistant"}),
            ],
        )

    @pytest.mark.skipif(sys.version_info >= (3, 11, 0), reason="Python <3.11 required")
    def test_llmobs_chain_batch(
        self, langchain_core, langchain_openai, mock_llmobs_span_writer, mock_tracer, request_vcr
    ):
        prompt = langchain_core.prompts.ChatPromptTemplate.from_template("Tell me a short joke about {topic}")
        output_parser = langchain_core.output_parsers.StrOutputParser()
        model = langchain_openai.ChatOpenAI()
        chain = {"topic": langchain_core.runnables.RunnablePassthrough()} | prompt | model | output_parser

        self._test_llmobs_chain_invoke(
            generate_trace=lambda inputs: chain.batch(inputs=["chickens", "pigs"]),
            request_vcr=request_vcr,
            mock_llmobs_span_writer=mock_llmobs_span_writer,
            mock_tracer=mock_tracer,
            cassette_name="lcel_openai_chain_batch.yaml",
            expected_spans_data=[
                (
                    "chain",
                    {
                        "input_value": json.dumps(["chickens", "pigs"]),
                        "output_value": mock.ANY,
                    },
                ),
                ("llm", {"provider": "openai", "input_roles": ["user"], "output_role": "assistant"}),
                ("llm", {"provider": "openai", "input_roles": ["user"], "output_role": "assistant"}),
            ],
        )

    @flaky(1735812000)
    def test_llmobs_chain_schema_io(
        self, langchain_core, langchain_openai, mock_llmobs_span_writer, mock_tracer, request_vcr
    ):
        model = langchain_openai.ChatOpenAI()
        prompt = langchain_core.prompts.ChatPromptTemplate.from_messages(
            [
                ("system", "You're an assistant who's good at {ability}. Respond in 20 words or fewer"),
                langchain_core.prompts.MessagesPlaceholder(variable_name="history"),
                ("human", "{input}"),
            ]
        )

        chain = prompt | model

        self._test_llmobs_chain_invoke(
            generate_trace=lambda inputs: chain.invoke(
                {
                    "ability": "world capitals",
                    "history": [
                        langchain.schema.HumanMessage(content="Can you be my science teacher instead?"),
                        langchain.schema.AIMessage(content="Yes"),
                    ],
                    "input": "What's the powerhouse of the cell?",
                }
            ),
            request_vcr=request_vcr,
            mock_llmobs_span_writer=mock_llmobs_span_writer,
            mock_tracer=mock_tracer,
            cassette_name="lcel_openai_chain_schema_io.yaml",
            expected_spans_data=[
                (
                    "chain",
                    {
                        "input_value": json.dumps(
                            [
                                {
                                    "ability": "world capitals",
                                    "history": [
                                        ["user", "Can you be my science teacher instead?"],
                                        ["assistant", "Yes"],
                                    ],
                                    "input": "What's the powerhouse of the cell?",
                                }
                            ]
                        ),
                        "output_value": json.dumps(["assistant", "Mitochondria."]),
                    },
                ),
                (
                    "llm",
                    {
                        "provider": "openai",
                        "input_roles": ["system", "user", "assistant", "user"],
                        "output_role": "assistant",
                    },
                ),
            ],
        )
