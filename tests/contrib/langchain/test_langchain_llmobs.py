import json
import os
import sys

import mock
import pytest

from ddtrace.contrib.langchain.patch import SHOULD_PATCH_LANGCHAIN_COMMUNITY
from ddtrace.llmobs import LLMObs
from tests.contrib.langchain.utils import get_request_vcr
from tests.llmobs._utils import _expected_llmobs_llm_span_event
from tests.llmobs._utils import _expected_llmobs_non_llm_span_event


pytestmark = pytest.mark.skipif(
    SHOULD_PATCH_LANGCHAIN_COMMUNITY, reason="This module does not test langchain_community"
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
    def _expected_llmobs_chain_call(span, metadata=None, input_value=None, output_value=None):
        return _expected_llmobs_non_llm_span_event(
            span,
            span_kind="workflow",
            metadata=metadata,
            input_value=input_value,
            output_value=output_value,
            tags={
                "ml_app": "langchain_test",
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
                "ml_app": "langchain_test",
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
        different_py39_cassette=False,
    ):
        LLMObs.enable(ml_app="langchain_test", integrations_enabled=False, _tracer=mock_tracer)

        if sys.version_info < (3, 10, 0) and different_py39_cassette:
            cassette_name = cassette_name.replace(".yaml", "_39.yaml")
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
        different_py39_cassette=False,
    ):
        LLMObs.enable(ml_app="langchain_test", integrations_enabled=False, _tracer=mock_tracer)

        if sys.version_info < (3, 10, 0) and different_py39_cassette:
            cassette_name = cassette_name.replace(".yaml", "_39.yaml")
        with request_vcr.use_cassette(cassette_name):
            generate_trace("Can you explain what an LLM chain is?")
        trace = mock_tracer.pop_traces()[0]

        expected_llmobs_writer_calls = cls._expected_llmobs_chain_calls(
            trace=trace, expected_spans_data=expected_spans_data
        )
        assert mock_llmobs_span_writer.enqueue.call_count == len(expected_spans_data)
        mock_llmobs_span_writer.assert_has_calls(expected_llmobs_writer_calls)
        LLMObs.disable()

    def test_llmobs_openai_llm(self, langchain, mock_llmobs_span_writer, mock_tracer, request_vcr):
        llm = langchain.llms.OpenAI()

        self._test_llmobs_llm_invoke(
            generate_trace=llm,
            request_vcr=request_vcr,
            mock_llmobs_span_writer=mock_llmobs_span_writer,
            mock_tracer=mock_tracer,
            cassette_name="openai_completion_sync.yaml",
            different_py39_cassette=True,
            provider="openai",
        )

    def test_llmobs_cohere_llm(self, langchain, mock_llmobs_span_writer, mock_tracer, request_vcr):
        llm = langchain.llms.Cohere(model="cohere.command-light-text-v14")

        self._test_llmobs_llm_invoke(
            generate_trace=llm,
            request_vcr=request_vcr,
            mock_llmobs_span_writer=mock_llmobs_span_writer,
            mock_tracer=mock_tracer,
            cassette_name="cohere_completion_sync.yaml",
            provider="cohere",
        )

    def test_llmobs_ai21_llm(self, langchain, mock_llmobs_span_writer, mock_tracer, request_vcr):
        llm = langchain.llms.AI21()

        self._test_llmobs_llm_invoke(
            generate_trace=llm,
            request_vcr=request_vcr,
            mock_llmobs_span_writer=mock_llmobs_span_writer,
            mock_tracer=mock_tracer,
            cassette_name="ai21_completion_sync.yaml",
            provider="ai21",
            different_py39_cassette=True,
        )

    def test_llmobs_huggingfacehub_llm(self, langchain, mock_llmobs_span_writer, mock_tracer, request_vcr):
        llm = langchain.llms.HuggingFaceHub(
            repo_id="google/flan-t5-xxl",
            model_kwargs={"temperature": 0.0, "max_tokens": 256},
            huggingfacehub_api_token=os.getenv("HUGGINGFACEHUB_API_TOKEN", "<not-a-real-key>"),
        )

        self._test_llmobs_llm_invoke(
            generate_trace=llm,
            request_vcr=request_vcr,
            mock_llmobs_span_writer=mock_llmobs_span_writer,
            mock_tracer=mock_tracer,
            cassette_name="huggingfacehub_completion_sync.yaml",
            provider="huggingface_hub",
        )

    def test_llmobs_openai_chat_model(self, langchain, mock_llmobs_span_writer, mock_tracer, request_vcr):
        chat = langchain.chat_models.ChatOpenAI(temperature=0, max_tokens=256)

        self._test_llmobs_llm_invoke(
            generate_trace=lambda prompt: chat([langchain.schema.HumanMessage(content=prompt)]),
            request_vcr=request_vcr,
            mock_llmobs_span_writer=mock_llmobs_span_writer,
            mock_tracer=mock_tracer,
            cassette_name="openai_chat_completion_sync_call.yaml",
            provider="openai",
            input_roles=["user"],
            output_role="assistant",
            different_py39_cassette=True,
        )

    def test_llmobs_openai_chat_model_custom_role(self, langchain, mock_llmobs_span_writer, mock_tracer, request_vcr):
        chat = langchain.chat_models.ChatOpenAI(temperature=0, max_tokens=256)

        self._test_llmobs_llm_invoke(
            generate_trace=lambda prompt: chat([langchain.schema.ChatMessage(content=prompt, role="custom")]),
            request_vcr=request_vcr,
            mock_llmobs_span_writer=mock_llmobs_span_writer,
            mock_tracer=mock_tracer,
            cassette_name="openai_chat_completion_sync_call.yaml",
            provider="openai",
            input_roles=["custom"],
            output_role="assistant",
            different_py39_cassette=True,
        )

    def test_llmobs_chain(self, langchain, mock_llmobs_span_writer, mock_tracer, request_vcr):
        chain = langchain.chains.LLMMathChain(llm=langchain.llms.OpenAI(temperature=0, max_tokens=256))

        self._test_llmobs_chain_invoke(
            generate_trace=lambda prompt: chain.run("what is two raised to the fifty-fourth power?"),
            request_vcr=request_vcr,
            mock_llmobs_span_writer=mock_llmobs_span_writer,
            mock_tracer=mock_tracer,
            cassette_name="openai_math_chain_sync.yaml",
            expected_spans_data=[
                (
                    "chain",
                    {
                        "input_value": json.dumps({"question": "what is two raised to the fifty-fourth power?"}),
                        "output_value": json.dumps(
                            {
                                "question": "what is two raised to the fifty-fourth power?",
                                "answer": "Answer: 18014398509481984",
                            }
                        ),
                    },
                ),
                (
                    "chain",
                    {
                        "input_value": json.dumps(
                            {
                                "question": "what is two raised to the fifty-fourth power?",
                                "stop": ["```output"],
                            }
                        ),
                        "output_value": json.dumps(
                            {
                                "question": "what is two raised to the fifty-fourth power?",
                                "stop": ["```output"],
                                "text": '\n```text\n2**54\n```\n...numexpr.evaluate("2**54")...\n',
                            }
                        ),
                    },
                ),
                ("llm", {"provider": "openai", "input_roles": [None], "output_role": None}),
            ],
            different_py39_cassette=True,
        )

    @pytest.mark.skipif(sys.version_info < (3, 10, 0), reason="Requires unnecessary cassette file for Python 3.9")
    def test_llmobs_chain_nested(self, langchain, mock_llmobs_span_writer, mock_tracer, request_vcr):
        template = """Paraphrase this text:

            {input_text}

            Paraphrase: """
        prompt = langchain.PromptTemplate(input_variables=["input_text"], template=template)
        style_paraphrase_chain = langchain.chains.LLMChain(
            llm=langchain.llms.OpenAI(model="gpt-3.5-turbo-instruct"), prompt=prompt, output_key="paraphrased_output"
        )
        rhyme_template = """Make this text rhyme:

            {paraphrased_output}

            Rhyme: """
        rhyme_prompt = langchain.PromptTemplate(input_variables=["paraphrased_output"], template=rhyme_template)
        rhyme_chain = langchain.chains.LLMChain(
            llm=langchain.llms.OpenAI(model="gpt-3.5-turbo-instruct"), prompt=rhyme_prompt, output_key="final_output"
        )
        sequential_chain = langchain.chains.SequentialChain(
            chains=[style_paraphrase_chain, rhyme_chain],
            input_variables=["input_text"],
            output_variables=["final_output"],
        )

        input_text = """
            I have convinced myself that there is absolutely nothing in the world, no sky, no earth, no minds, no
            bodies. Does it now follow that I too do not exist? No: if I convinced myself of something then I certainly
            existed. But there is a deceiver of supreme power and cunning who is deliberately and constantly deceiving
            me. In that case I too undoubtedly exist, if he is deceiving me; and let him deceive me as much as he can,
            he will never bring it about that I am nothing so long as I think that I am something. So after considering
            everything very thoroughly, I must finally conclude that this proposition, I am, I exist, is necessarily
            true whenever it is put forward by me or conceived in my mind.
            """

        self._test_llmobs_chain_invoke(
            generate_trace=lambda prompt: sequential_chain.run({"input_text": input_text}),
            request_vcr=request_vcr,
            mock_llmobs_span_writer=mock_llmobs_span_writer,
            mock_tracer=mock_tracer,
            cassette_name="openai_sequential_paraphrase_and_rhyme_sync.yaml",
            expected_spans_data=[
                (
                    "chain",
                    {
                        "input_value": json.dumps({"input_text": input_text}),
                        "output_value": mock.ANY,
                    },
                ),
                (
                    "chain",
                    {
                        "input_value": json.dumps({"input_text": input_text}),
                        "output_value": mock.ANY,
                    },
                ),
                ("llm", {"provider": "openai", "input_roles": [None], "output_role": None}),
                (
                    "chain",
                    {
                        "input_value": mock.ANY,
                        "output_value": mock.ANY,
                    },
                ),
                ("llm", {"provider": "openai", "input_roles": [None], "output_role": None}),
            ],
        )

    @pytest.mark.skipif(sys.version_info < (3, 10, 0), reason="Requires unnecessary cassette file for Python 3.9")
    def test_llmobs_chain_schema_io(self, langchain, mock_llmobs_span_writer, mock_tracer, request_vcr):
        model = langchain.chat_models.ChatOpenAI(temperature=0, max_tokens=256)
        prompt = langchain.prompts.ChatPromptTemplate.from_messages(
            [
                langchain.prompts.SystemMessagePromptTemplate.from_template(
                    "You're an assistant who's good at {ability}. Respond in 20 words or fewer"
                ),
                langchain.prompts.MessagesPlaceholder(variable_name="history"),
                langchain.prompts.HumanMessagePromptTemplate.from_template("{input}"),
            ]
        )

        chain = langchain.chains.LLMChain(prompt=prompt, llm=model)

        self._test_llmobs_chain_invoke(
            generate_trace=lambda input_text: chain.run(
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
            cassette_name="openai_chain_schema_io.yaml",
            expected_spans_data=[
                (
                    "chain",
                    {
                        "input_value": json.dumps(
                            {
                                "ability": "world capitals",
                                "history": [["user", "Can you be my science teacher instead?"], ["assistant", "Yes"]],
                                "input": "What's the powerhouse of the cell?",
                            }
                        ),
                        "output_value": json.dumps(
                            {
                                "ability": "world capitals",
                                "history": [["user", "Can you be my science teacher instead?"], ["assistant", "Yes"]],
                                "input": "What's the powerhouse of the cell?",
                                "text": "Mitochondria.",
                            }
                        ),
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
