import json
from operator import itemgetter
import os
import sys

import mock
import pytest

from ddtrace import patch
from ddtrace.contrib.langchain.patch import PATCH_LANGCHAIN_V0
from ddtrace.llmobs import LLMObs
from tests.contrib.langchain.utils import get_request_vcr
from tests.contrib.langchain.utils import long_input_text
from tests.llmobs._utils import _expected_llmobs_llm_span_event
from tests.llmobs._utils import _expected_llmobs_non_llm_span_event
from tests.subprocesstest import SubprocessTestCase
from tests.subprocesstest import run_in_subprocess
from tests.utils import flaky


if PATCH_LANGCHAIN_V0:
    from langchain.schema import AIMessage
    from langchain.schema import ChatMessage
    from langchain.schema import HumanMessage
else:
    from langchain_core.messages import AIMessage
    from langchain_core.messages import ChatMessage
    from langchain_core.messages import HumanMessage


def _assert_expected_llmobs_llm_span(span, mock_llmobs_span_writer, input_role=None, mock_io=False):
    provider = span.get_tag("langchain.request.provider")

    metadata = {}
    temperature_key = "temperature"
    if provider == "huggingface_hub":
        temperature_key = "model_kwargs.temperature"
        max_tokens_key = "model_kwargs.max_tokens"
    elif provider == "ai21":
        max_tokens_key = "maxTokens"
    else:
        max_tokens_key = "max_tokens"
    temperature = span.get_tag(f"langchain.request.{provider}.parameters.{temperature_key}")
    max_tokens = span.get_tag(f"langchain.request.{provider}.parameters.{max_tokens_key}")
    if temperature is not None:
        metadata["temperature"] = float(temperature)
    if max_tokens is not None:
        metadata["max_tokens"] = int(max_tokens)

    input_messages = [{"content": mock.ANY}]
    output_messages = [{"content": mock.ANY}]
    if input_role is not None:
        input_messages[0]["role"] = input_role
        output_messages[0]["role"] = "assistant"

    mock_llmobs_span_writer.enqueue.assert_any_call(
        _expected_llmobs_llm_span_event(
            span,
            model_name=span.get_tag("langchain.request.model"),
            model_provider=span.get_tag("langchain.request.provider"),
            input_messages=input_messages if not mock_io else mock.ANY,
            output_messages=output_messages if not mock_io else mock.ANY,
            metadata=metadata,
            token_metrics={},
            tags={"ml_app": "langchain_test"},
            integration="langchain",
        )
    )


def _assert_expected_llmobs_chain_span(span, mock_llmobs_span_writer, input_value=None, output_value=None):
    expected_chain_span_event = _expected_llmobs_non_llm_span_event(
        span,
        "workflow",
        input_value=input_value if input_value is not None else mock.ANY,
        output_value=output_value if output_value is not None else mock.ANY,
        tags={"ml_app": "langchain_test"},
        integration="langchain",
    )
    mock_llmobs_span_writer.enqueue.assert_any_call(expected_chain_span_event)


class BaseTestLLMObsLangchain:
    cassette_subdirectory_name = "langchain"
    ml_app = "langchain_test"

    @classmethod
    def _invoke_llm(cls, llm, prompt, mock_tracer, cassette_name):
        LLMObs.enable(ml_app=cls.ml_app, integrations_enabled=False, _tracer=mock_tracer)
        with get_request_vcr(subdirectory_name=cls.cassette_subdirectory_name).use_cassette(cassette_name):
            if PATCH_LANGCHAIN_V0:
                llm(prompt)
            else:
                llm.invoke(prompt)
        LLMObs.disable()
        return mock_tracer.pop_traces()[0][0]

    @classmethod
    def _invoke_chat(cls, chat_model, prompt, mock_tracer, cassette_name, role="user"):
        LLMObs.enable(ml_app=cls.ml_app, integrations_enabled=False, _tracer=mock_tracer)
        with get_request_vcr(subdirectory_name=cls.cassette_subdirectory_name).use_cassette(cassette_name):
            if role == "user":
                messages = [HumanMessage(content=prompt)]
            else:
                messages = [ChatMessage(content=prompt, role="custom")]
            if PATCH_LANGCHAIN_V0:
                chat_model(messages)
            else:
                chat_model.invoke(messages)
        LLMObs.disable()
        return mock_tracer.pop_traces()[0][0]

    @classmethod
    def _invoke_chain(cls, chain, prompt, mock_tracer, cassette_name, batch=False):
        LLMObs.enable(ml_app=cls.ml_app, integrations_enabled=False, _tracer=mock_tracer)
        with get_request_vcr(subdirectory_name=cls.cassette_subdirectory_name).use_cassette(cassette_name):
            if batch:
                chain.batch(inputs=prompt)
            elif PATCH_LANGCHAIN_V0:
                chain.run(prompt)
            else:
                chain.invoke(prompt)
        LLMObs.disable()
        return mock_tracer.pop_traces()[0]


@pytest.mark.skipif(not PATCH_LANGCHAIN_V0, reason="These tests are for langchain < 0.1.0")
class TestLLMObsLangchain(BaseTestLLMObsLangchain):
    cassette_subdirectory_name = "langchain"

    @pytest.mark.skipif(sys.version_info < (3, 10, 0), reason="Requires unnecessary cassette file for Python 3.9")
    def test_llmobs_openai_llm(self, langchain, mock_llmobs_span_writer, mock_tracer):
        span = self._invoke_llm(
            llm=langchain.llms.OpenAI(model="gpt-3.5-turbo-instruct"),
            prompt="Can you explain what Descartes meant by 'I think, therefore I am'?",
            mock_tracer=mock_tracer,
            cassette_name="openai_completion_sync.yaml",
        )
        assert mock_llmobs_span_writer.enqueue.call_count == 1
        _assert_expected_llmobs_llm_span(span, mock_llmobs_span_writer)

    def test_llmobs_cohere_llm(self, langchain, mock_llmobs_span_writer, mock_tracer):
        span = self._invoke_llm(
            llm=langchain.llms.Cohere(model="cohere.command-light-text-v14"),
            prompt="Can you explain what Descartes meant by 'I think, therefore I am'?",
            mock_tracer=mock_tracer,
            cassette_name="cohere_completion_sync.yaml",
        )
        assert mock_llmobs_span_writer.enqueue.call_count == 1
        _assert_expected_llmobs_llm_span(span, mock_llmobs_span_writer)

    @pytest.mark.skipif(sys.version_info < (3, 10, 0), reason="Requires unnecessary cassette file for Python 3.9")
    def test_llmobs_ai21_llm(self, langchain, mock_llmobs_span_writer, mock_tracer):
        llm = langchain.llms.AI21()
        span = self._invoke_llm(
            llm=llm,
            prompt="Can you explain what Descartes meant by 'I think, therefore I am'?",
            mock_tracer=mock_tracer,
            cassette_name="ai21_completion_sync.yaml",
        )
        assert mock_llmobs_span_writer.enqueue.call_count == 1
        _assert_expected_llmobs_llm_span(span, mock_llmobs_span_writer)

    def test_llmobs_huggingfacehub_llm(self, langchain, mock_llmobs_span_writer, mock_tracer):
        llm = langchain.llms.HuggingFaceHub(
            repo_id="google/flan-t5-xxl",
            model_kwargs={"temperature": 0.0, "max_tokens": 256},
            huggingfacehub_api_token=os.getenv("HUGGINGFACEHUB_API_TOKEN", "<not-a-real-key>"),
        )
        span = self._invoke_llm(
            llm=llm,
            prompt="Can you explain what Descartes meant by 'I think, therefore I am'?",
            mock_tracer=mock_tracer,
            cassette_name="huggingfacehub_completion_sync.yaml",
        )
        assert mock_llmobs_span_writer.enqueue.call_count == 1
        _assert_expected_llmobs_llm_span(span, mock_llmobs_span_writer)

    @pytest.mark.skipif(sys.version_info < (3, 10, 0), reason="Requires unnecessary cassette file for Python 3.9")
    def test_llmobs_openai_chat_model(self, langchain, mock_llmobs_span_writer, mock_tracer):
        chat = langchain.chat_models.ChatOpenAI(temperature=0, max_tokens=256)
        span = self._invoke_chat(
            chat_model=chat,
            prompt="When do you use 'whom' instead of 'who'?",
            mock_tracer=mock_tracer,
            cassette_name="openai_chat_completion_sync_call.yaml",
        )
        assert mock_llmobs_span_writer.enqueue.call_count == 1
        _assert_expected_llmobs_llm_span(span, mock_llmobs_span_writer, input_role="user")

    @pytest.mark.skipif(sys.version_info < (3, 10, 0), reason="Requires unnecessary cassette file for Python 3.9")
    def test_llmobs_openai_chat_model_custom_role(self, langchain, mock_llmobs_span_writer, mock_tracer):
        chat = langchain.chat_models.ChatOpenAI(temperature=0, max_tokens=256)
        span = self._invoke_chat(
            chat_model=chat,
            prompt="When do you use 'whom' instead of 'who'?",
            mock_tracer=mock_tracer,
            cassette_name="openai_chat_completion_sync_call.yaml",
            role="custom",
        )
        assert mock_llmobs_span_writer.enqueue.call_count == 1
        _assert_expected_llmobs_llm_span(span, mock_llmobs_span_writer, input_role="custom")

    @pytest.mark.skipif(sys.version_info < (3, 10, 0), reason="Requires unnecessary cassette file for Python 3.9")
    def test_llmobs_chain(self, langchain, mock_llmobs_span_writer, mock_tracer):
        chain = langchain.chains.LLMMathChain(llm=langchain.llms.OpenAI(temperature=0, max_tokens=256))

        trace = self._invoke_chain(
            chain=chain,
            prompt="what is two raised to the fifty-fourth power?",
            mock_tracer=mock_tracer,
            cassette_name="openai_math_chain_sync.yaml",
        )
        assert mock_llmobs_span_writer.enqueue.call_count == 3
        _assert_expected_llmobs_chain_span(
            trace[0],
            mock_llmobs_span_writer,
            input_value=json.dumps({"question": "what is two raised to the fifty-fourth power?"}),
            output_value=json.dumps(
                {"question": "what is two raised to the fifty-fourth power?", "answer": "Answer: 18014398509481984"}
            ),
        )
        _assert_expected_llmobs_chain_span(
            trace[1],
            mock_llmobs_span_writer,
            input_value=json.dumps(
                {"question": "what is two raised to the fifty-fourth power?", "stop": ["```output"]}
            ),
            output_value=json.dumps(
                {
                    "question": "what is two raised to the fifty-fourth power?",
                    "stop": ["```output"],
                    "text": '\n```text\n2**54\n```\n...numexpr.evaluate("2**54")...\n',
                }
            ),
        )
        _assert_expected_llmobs_llm_span(trace[2], mock_llmobs_span_writer)

    @pytest.mark.skipif(sys.version_info < (3, 10, 0), reason="Requires unnecessary cassette file for Python 3.9")
    def test_llmobs_chain_nested(self, langchain, mock_llmobs_span_writer, mock_tracer):
        template = "Paraphrase this text:\n{input_text}\nParaphrase: "
        prompt = langchain.PromptTemplate(input_variables=["input_text"], template=template)
        style_paraphrase_chain = langchain.chains.LLMChain(
            llm=langchain.llms.OpenAI(model="gpt-3.5-turbo-instruct"), prompt=prompt, output_key="paraphrased_output"
        )
        rhyme_template = "Make this text rhyme:\n{paraphrased_output}\nRhyme: "
        rhyme_prompt = langchain.PromptTemplate(input_variables=["paraphrased_output"], template=rhyme_template)
        rhyme_chain = langchain.chains.LLMChain(
            llm=langchain.llms.OpenAI(model="gpt-3.5-turbo-instruct"), prompt=rhyme_prompt, output_key="final_output"
        )
        sequential_chain = langchain.chains.SequentialChain(
            chains=[style_paraphrase_chain, rhyme_chain],
            input_variables=["input_text"],
            output_variables=["final_output"],
        )
        input_text = long_input_text
        trace = self._invoke_chain(
            chain=sequential_chain,
            prompt={"input_text": input_text},
            mock_tracer=mock_tracer,
            cassette_name="openai_sequential_paraphrase_and_rhyme_sync.yaml",
        )
        assert mock_llmobs_span_writer.enqueue.call_count == 5
        _assert_expected_llmobs_chain_span(
            trace[0],
            mock_llmobs_span_writer,
            input_value=json.dumps({"input_text": input_text}),
        )
        _assert_expected_llmobs_chain_span(
            trace[1],
            mock_llmobs_span_writer,
            input_value=json.dumps({"input_text": input_text}),
        )
        _assert_expected_llmobs_llm_span(trace[2], mock_llmobs_span_writer)
        _assert_expected_llmobs_chain_span(trace[3], mock_llmobs_span_writer)
        _assert_expected_llmobs_llm_span(trace[4], mock_llmobs_span_writer)

    @pytest.mark.skipif(sys.version_info < (3, 10, 0), reason="Requires unnecessary cassette file for Python 3.9")
    def test_llmobs_chain_schema_io(self, langchain, mock_llmobs_span_writer, mock_tracer):
        prompt = langchain.prompts.ChatPromptTemplate.from_messages(
            [
                langchain.prompts.SystemMessagePromptTemplate.from_template(
                    "You're an assistant who's good at {ability}. Respond in 20 words or fewer"
                ),
                langchain.prompts.MessagesPlaceholder(variable_name="history"),
                langchain.prompts.HumanMessagePromptTemplate.from_template("{input}"),
            ]
        )
        chain = langchain.chains.LLMChain(
            prompt=prompt, llm=langchain.chat_models.ChatOpenAI(temperature=0, max_tokens=256)
        )
        trace = self._invoke_chain(
            chain=chain,
            prompt={
                "ability": "world capitals",
                "history": [
                    HumanMessage(content="Can you be my science teacher instead?"),
                    AIMessage(content="Yes"),
                ],
                "input": "What's the powerhouse of the cell?",
            },
            mock_tracer=mock_tracer,
            cassette_name="openai_chain_schema_io.yaml",
        )
        assert mock_llmobs_span_writer.enqueue.call_count == 2
        _assert_expected_llmobs_chain_span(
            trace[0],
            mock_llmobs_span_writer,
            input_value=json.dumps(
                {
                    "ability": "world capitals",
                    "history": [["user", "Can you be my science teacher instead?"], ["assistant", "Yes"]],
                    "input": "What's the powerhouse of the cell?",
                }
            ),
            output_value=json.dumps(
                {
                    "ability": "world capitals",
                    "history": [["user", "Can you be my science teacher instead?"], ["assistant", "Yes"]],
                    "input": "What's the powerhouse of the cell?",
                    "text": "Mitochondria.",
                }
            ),
        )
        _assert_expected_llmobs_llm_span(trace[1], mock_llmobs_span_writer, mock_io=True)


@flaky(1735812000, reason="Community cassette tests are flaky")
@pytest.mark.skipif(PATCH_LANGCHAIN_V0, reason="These tests are for langchain >= 0.1.0")
class TestLLMObsLangchainCommunity(BaseTestLLMObsLangchain):
    cassette_subdirectory_name = "langchain_community"

    def test_llmobs_openai_llm(self, langchain_openai, mock_llmobs_span_writer, mock_tracer):
        span = self._invoke_llm(
            llm=langchain_openai.OpenAI(),
            prompt="Can you explain what Descartes meant by 'I think, therefore I am'?",
            mock_tracer=mock_tracer,
            cassette_name="openai_completion_sync.yaml",
        )
        assert mock_llmobs_span_writer.enqueue.call_count == 1
        _assert_expected_llmobs_llm_span(span, mock_llmobs_span_writer)

    def test_llmobs_cohere_llm(self, langchain_community, mock_llmobs_span_writer, mock_tracer):
        if langchain_community is None:
            pytest.skip("langchain-community not installed which is required for this test.")
        span = self._invoke_llm(
            llm=langchain_community.llms.Cohere(model="cohere.command-light-text-v14"),
            prompt="What is the secret Krabby Patty recipe?",
            mock_tracer=mock_tracer,
            cassette_name="cohere_completion_sync.yaml",
        )
        assert mock_llmobs_span_writer.enqueue.call_count == 1
        _assert_expected_llmobs_llm_span(span, mock_llmobs_span_writer)

    @pytest.mark.skipif(sys.version_info < (3, 10, 0), reason="Requires unnecessary cassette file for Python 3.9")
    def test_llmobs_ai21_llm(self, langchain_community, mock_llmobs_span_writer, mock_tracer):
        if langchain_community is None:
            pytest.skip("langchain-community not installed which is required for this test.")
        span = self._invoke_llm(
            llm=langchain_community.llms.AI21(),
            prompt="Why does everyone in Bikini Bottom hate Plankton?",
            mock_tracer=mock_tracer,
            cassette_name="ai21_completion_sync.yaml",
        )
        assert mock_llmobs_span_writer.enqueue.call_count == 1
        _assert_expected_llmobs_llm_span(span, mock_llmobs_span_writer)

    def test_llmobs_openai_chat_model(self, langchain_openai, mock_llmobs_span_writer, mock_tracer):
        span = self._invoke_chat(
            chat_model=langchain_openai.ChatOpenAI(temperature=0, max_tokens=256),
            prompt="When do you use 'who' instead of 'whom'?",
            mock_tracer=mock_tracer,
            cassette_name="openai_chat_completion_sync_call.yaml",
            role="user",
        )
        assert mock_llmobs_span_writer.enqueue.call_count == 1
        _assert_expected_llmobs_llm_span(span, mock_llmobs_span_writer, input_role="user")

    def test_llmobs_openai_chat_model_custom_role(self, langchain_openai, mock_llmobs_span_writer, mock_tracer):
        span = self._invoke_chat(
            chat_model=langchain_openai.ChatOpenAI(temperature=0, max_tokens=256),
            prompt="When do you use 'who' instead of 'whom'?",
            mock_tracer=mock_tracer,
            cassette_name="openai_chat_completion_sync_call.yaml",
            role="custom",
        )
        assert mock_llmobs_span_writer.enqueue.call_count == 1
        _assert_expected_llmobs_llm_span(span, mock_llmobs_span_writer, input_role="custom")

    def test_llmobs_chain(self, langchain_core, langchain_openai, mock_llmobs_span_writer, mock_tracer):
        prompt = langchain_core.prompts.ChatPromptTemplate.from_messages(
            [("system", "You are world class technical documentation writer."), ("user", "{input}")]
        )
        chain = prompt | langchain_openai.OpenAI()
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
        trace = self._invoke_chain(
            chain=chain,
            prompt={"input": "Can you explain what an LLM chain is?"},
            mock_tracer=mock_tracer,
            cassette_name="lcel_openai_chain_call.yaml",
        )
        assert mock_llmobs_span_writer.enqueue.call_count == 2
        _assert_expected_llmobs_chain_span(
            trace[0],
            mock_llmobs_span_writer,
            input_value=json.dumps([{"input": "Can you explain what an LLM chain is?"}]),
            output_value=expected_output,
        )
        _assert_expected_llmobs_llm_span(trace[1], mock_llmobs_span_writer)

    def test_llmobs_chain_nested(self, langchain_core, langchain_openai, mock_llmobs_span_writer, mock_tracer):
        prompt1 = langchain_core.prompts.ChatPromptTemplate.from_template("what is the city {person} is from?")
        prompt2 = langchain_core.prompts.ChatPromptTemplate.from_template(
            "what country is the city {city} in? respond in {language}"
        )
        model = langchain_openai.ChatOpenAI()
        chain1 = prompt1 | model | langchain_core.output_parsers.StrOutputParser()
        chain2 = prompt2 | model | langchain_core.output_parsers.StrOutputParser()
        complete_chain = {"city": chain1, "language": itemgetter("language")} | chain2
        trace = self._invoke_chain(
            chain=complete_chain,
            prompt={"person": "Spongebob Squarepants", "language": "Spanish"},
            mock_tracer=mock_tracer,
            cassette_name="lcel_openai_chain_nested.yaml",
        )
        assert mock_llmobs_span_writer.enqueue.call_count == 4
        _assert_expected_llmobs_chain_span(
            trace[0],
            mock_llmobs_span_writer,
            input_value=json.dumps([{"person": "Spongebob Squarepants", "language": "Spanish"}]),
            output_value=mock.ANY,
        )
        _assert_expected_llmobs_chain_span(
            trace[1],
            mock_llmobs_span_writer,
            input_value=json.dumps([{"person": "Spongebob Squarepants", "language": "Spanish"}]),
            output_value=mock.ANY,
        )
        _assert_expected_llmobs_llm_span(trace[2], mock_llmobs_span_writer, input_role="user")
        _assert_expected_llmobs_llm_span(trace[3], mock_llmobs_span_writer, input_role="user")

    @pytest.mark.skipif(sys.version_info >= (3, 11, 0), reason="Python <3.11 required")
    def test_llmobs_chain_batch(self, langchain_core, langchain_openai, mock_llmobs_span_writer, mock_tracer):
        prompt = langchain_core.prompts.ChatPromptTemplate.from_template("Tell me a short joke about {topic}")
        output_parser = langchain_core.output_parsers.StrOutputParser()
        model = langchain_openai.ChatOpenAI()
        chain = {"topic": langchain_core.runnables.RunnablePassthrough()} | prompt | model | output_parser

        trace = self._invoke_chain(
            chain=chain,
            prompt=["chickens", "pigs"],
            mock_tracer=mock_tracer,
            cassette_name="lcel_openai_chain_batch.yaml",
            batch=True,
        )
        assert mock_llmobs_span_writer.enqueue.call_count == 3
        _assert_expected_llmobs_chain_span(
            trace[0],
            mock_llmobs_span_writer,
            input_value=json.dumps(["chickens", "pigs"]),
            output_value=mock.ANY,
        )
        _assert_expected_llmobs_llm_span(trace[1], mock_llmobs_span_writer, input_role="user")
        _assert_expected_llmobs_llm_span(trace[2], mock_llmobs_span_writer, input_role="user")

    def test_llmobs_chain_schema_io(self, langchain_core, langchain_openai, mock_llmobs_span_writer, mock_tracer):
        prompt = langchain_core.prompts.ChatPromptTemplate.from_messages(
            [
                ("system", "You're an assistant who's good at {ability}. Respond in 20 words or fewer"),
                langchain_core.prompts.MessagesPlaceholder(variable_name="history"),
                ("human", "{input}"),
            ]
        )
        chain = prompt | langchain_openai.ChatOpenAI()
        trace = self._invoke_chain(
            chain=chain,
            prompt={
                "ability": "world capitals",
                "history": [HumanMessage(content="Can you be my science teacher instead?"), AIMessage(content="Yes")],
                "input": "What's the powerhouse of the cell?",
            },
            mock_tracer=mock_tracer,
            cassette_name="lcel_openai_chain_schema_io.yaml",
        )
        assert mock_llmobs_span_writer.enqueue.call_count == 2
        _assert_expected_llmobs_chain_span(
            trace[0],
            mock_llmobs_span_writer,
            input_value=json.dumps(
                [
                    {
                        "ability": "world capitals",
                        "history": [["user", "Can you be my science teacher instead?"], ["assistant", "Yes"]],
                        "input": "What's the powerhouse of the cell?",
                    }
                ]
            ),
            output_value=json.dumps(["assistant", "Mitochondria."]),
        )
        _assert_expected_llmobs_llm_span(trace[1], mock_llmobs_span_writer, mock_io=True)

    def test_llmobs_anthropic_chat_model(self, langchain_anthropic, mock_llmobs_span_writer, mock_tracer):
        chat = langchain_anthropic.ChatAnthropic(temperature=0, model="claude-3-opus-20240229", max_tokens=15)
        span = self._invoke_chat(
            chat_model=chat,
            prompt="When do you use 'whom' instead of 'who'?",
            mock_tracer=mock_tracer,
            cassette_name="anthropic_chat_completion_sync.yaml",
        )
        assert mock_llmobs_span_writer.enqueue.call_count == 1
        _assert_expected_llmobs_llm_span(span, mock_llmobs_span_writer, input_role="user")


@flaky(1735812000, reason="Community cassette tests are flaky")
@pytest.mark.skipif(PATCH_LANGCHAIN_V0, reason="These tests are for langchain >= 0.1.0")
class TestLangchainTraceStructureWithLlmIntegrations(SubprocessTestCase):
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
        OPENAI_API_KEY="testing",
        DD_API_KEY="<not-a-real-key>",
    )

    anthropic_env_config = dict(
        ANTHROPIC_API_KEY="testing",
        DD_API_KEY="<not-a-real-key>",
    )

    def setUp(self):
        patcher = mock.patch("ddtrace.llmobs._llmobs.LLMObsSpanWriter")
        LLMObsSpanWriterMock = patcher.start()
        mock_llmobs_span_writer = mock.MagicMock()
        LLMObsSpanWriterMock.return_value = mock_llmobs_span_writer

        self.mock_llmobs_span_writer = mock_llmobs_span_writer

        super(TestLangchainTraceStructureWithLlmIntegrations, self).setUp()

    def tearDown(self):
        LLMObs.disable()

    def _assert_trace_structure_from_writer_call_args(self, span_kinds):
        assert self.mock_llmobs_span_writer.enqueue.call_count == len(span_kinds)

        calls = self.mock_llmobs_span_writer.enqueue.call_args_list

        for span_kind, call in zip(span_kinds, calls):
            call_args = call.args[0]

            assert call_args["meta"]["span.kind"] == span_kind
            if span_kind == "workflow":
                assert len(call_args["meta"]["input"]["value"]) > 0
                assert len(call_args["meta"]["output"]["value"]) > 0
            elif span_kind == "llm":
                assert len(call_args["meta"]["input"]["messages"]) > 0
                assert len(call_args["meta"]["output"]["messages"]) > 0

    @staticmethod
    def _call_bedrock_chat_model(ChatBedrock, HumanMessage):
        chat = ChatBedrock(
            model_id="amazon.titan-tg1-large",
            model_kwargs={"max_tokens": 50, "temperature": 0},
        )
        messages = [HumanMessage(content="summarize the plot to the lord of the rings in a dozen words")]
        with get_request_vcr(subdirectory_name="langchain_community").use_cassette("bedrock_amazon_chat_invoke.yaml"):
            chat.invoke(messages)

    @staticmethod
    def _call_bedrock_llm(Bedrock, ConversationChain, ConversationBufferMemory):
        llm = Bedrock(
            model_id="amazon.titan-tg1-large",
            region_name="us-east-1",
            model_kwargs={"temperature": 0, "topP": 0.9, "stopSequences": [], "maxTokens": 50},
        )

        conversation = ConversationChain(llm=llm, verbose=True, memory=ConversationBufferMemory())

        with get_request_vcr(subdirectory_name="langchain_community").use_cassette("bedrock_amazon_invoke.yaml"):
            conversation.predict(input="can you explain what Datadog is to someone not in the tech industry?")

    @staticmethod
    def _call_openai_llm(OpenAI):
        llm = OpenAI()
        with get_request_vcr(subdirectory_name="langchain_community").use_cassette("openai_completion_sync.yaml"):
            llm.invoke("Can you explain what Descartes meant by 'I think, therefore I am'?")

    @staticmethod
    def _call_anthropic_chat(Anthropic):
        llm = Anthropic(model="claude-3-opus-20240229", max_tokens=15)
        with get_request_vcr(subdirectory_name="langchain_community").use_cassette(
            "anthropic_chat_completion_sync.yaml"
        ):
            llm.invoke("When do you use 'whom' instead of 'who'?")

    @run_in_subprocess(env_overrides=bedrock_env_config)
    def test_llmobs_with_chat_model_bedrock_enabled(self):
        from langchain_aws import ChatBedrock
        from langchain_core.messages import HumanMessage

        patch(langchain=True, botocore=True)
        LLMObs.enable(ml_app="<ml-app-name>", integrations_enabled=False, agentless_enabled=True)

        self._call_bedrock_chat_model(ChatBedrock, HumanMessage)

        self._assert_trace_structure_from_writer_call_args(["workflow", "llm"])

    @run_in_subprocess(env_overrides=bedrock_env_config)
    def test_llmobs_with_chat_model_bedrock_disabled(self):
        from langchain_aws import ChatBedrock
        from langchain_core.messages import HumanMessage

        patch(langchain=True)
        LLMObs.enable(ml_app="<ml-app-name>", integrations_enabled=False, agentless_enabled=True)

        self._call_bedrock_chat_model(ChatBedrock, HumanMessage)

        self._assert_trace_structure_from_writer_call_args(["llm"])

    @run_in_subprocess(env_overrides=bedrock_env_config)
    def test_llmobs_with_llm_model_bedrock_enabled(self):
        from langchain.chains import ConversationChain
        from langchain.memory import ConversationBufferMemory

        try:
            from langchain_community.llms import Bedrock
        except (ImportError, ModuleNotFoundError):
            self.skipTest("langchain-community not installed which is required for this test.")

        patch(langchain=True, botocore=True)
        LLMObs.enable(ml_app="<ml-app-name>", integrations_enabled=False, agentless_enabled=True)
        self._call_bedrock_llm(Bedrock, ConversationChain, ConversationBufferMemory)
        self._assert_trace_structure_from_writer_call_args(["workflow", "workflow", "llm"])

    @run_in_subprocess(env_overrides=bedrock_env_config)
    def test_llmobs_with_llm_model_bedrock_disabled(self):
        from langchain.chains import ConversationChain
        from langchain.memory import ConversationBufferMemory

        try:
            from langchain_community.llms import Bedrock
        except (ImportError, ModuleNotFoundError):
            self.skipTest("langchain-community not installed which is required for this test.")

        patch(langchain=True)
        LLMObs.enable(ml_app="<ml-app-name>", integrations_enabled=False, agentless_enabled=True)
        self._call_bedrock_llm(Bedrock, ConversationChain, ConversationBufferMemory)
        self._assert_trace_structure_from_writer_call_args(["workflow", "llm"])

    @run_in_subprocess(env_overrides=openai_env_config)
    def test_llmobs_langchain_with_openai_enabled(self):
        from langchain_openai import OpenAI

        patch(langchain=True, openai=True)
        LLMObs.enable(ml_app="<ml-app-name>", integrations_enabled=False, agentless_enabled=True)
        self._call_openai_llm(OpenAI)
        self._assert_trace_structure_from_writer_call_args(["workflow", "llm"])

    @run_in_subprocess(env_overrides=openai_env_config)
    def test_llmobs_langchain_with_openai_disabled(self):
        from langchain_openai import OpenAI

        patch(langchain=True)

        LLMObs.enable(ml_app="<ml-app-name>", integrations_enabled=False, agentless_enabled=True)
        self._call_openai_llm(OpenAI)
        self._assert_trace_structure_from_writer_call_args(["llm"])

    @run_in_subprocess(env_overrides=anthropic_env_config)
    def test_llmobs_langchain_with_anthropic_enabled(self):
        from langchain_anthropic import ChatAnthropic

        patch(langchain=True, anthropic=True)

        LLMObs.enable(ml_app="<ml-app-name>", integrations_enabled=False, agentless_enabled=True)
        self._call_anthropic_chat(ChatAnthropic)
        self._assert_trace_structure_from_writer_call_args(["workflow", "llm"])

    @run_in_subprocess(env_overrides=anthropic_env_config)
    def test_llmobs_langchain_with_anthropic_disabled(self):
        from langchain_anthropic import ChatAnthropic

        patch(langchain=True)

        LLMObs.enable(ml_app="<ml-app-name>", integrations_enabled=False, agentless_enabled=True)

        self._call_anthropic_chat(ChatAnthropic)
        self._assert_trace_structure_from_writer_call_args(["llm"])
