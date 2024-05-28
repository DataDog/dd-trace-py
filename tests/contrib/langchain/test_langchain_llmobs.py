import json
from operator import itemgetter
import os
import sys

import mock
import pytest

from ddtrace.contrib.langchain.patch import SHOULD_PATCH_LANGCHAIN_COMMUNITY
from ddtrace.llmobs import LLMObs
from tests.contrib.langchain.utils import get_request_vcr
from tests.contrib.langchain.utils import long_input_text
from tests.llmobs._utils import _expected_llmobs_llm_span_event
from tests.llmobs._utils import _expected_llmobs_non_llm_span_event


if SHOULD_PATCH_LANGCHAIN_COMMUNITY:
    from langchain_core.messages import AIMessage
    from langchain_core.messages import ChatMessage
    from langchain_core.messages import HumanMessage
else:
    from langchain.schema import AIMessage
    from langchain.schema import ChatMessage
    from langchain.schema import HumanMessage


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
            if SHOULD_PATCH_LANGCHAIN_COMMUNITY:
                llm.invoke(prompt)
            else:
                llm(prompt)
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
            if SHOULD_PATCH_LANGCHAIN_COMMUNITY:
                chat_model.invoke(messages)
            else:
                chat_model(messages)
        LLMObs.disable()
        return mock_tracer.pop_traces()[0][0]

    @classmethod
    def _invoke_chain(cls, chain, prompt, mock_tracer, cassette_name, batch=False):
        LLMObs.enable(ml_app=cls.ml_app, integrations_enabled=False, _tracer=mock_tracer)
        with get_request_vcr(subdirectory_name=cls.cassette_subdirectory_name).use_cassette(cassette_name):
            if batch:
                chain.batch(inputs=prompt)
            elif SHOULD_PATCH_LANGCHAIN_COMMUNITY:
                chain.invoke(prompt)
            else:
                chain.run(prompt)
        LLMObs.disable()
        return mock_tracer.pop_traces()[0]


@pytest.mark.skipif(SHOULD_PATCH_LANGCHAIN_COMMUNITY, reason="These tests are for langchain < 0.1.0")
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


@pytest.mark.skipif(not SHOULD_PATCH_LANGCHAIN_COMMUNITY, reason="These tests are for langchain >= 0.1.0")
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
        span = self._invoke_llm(
            llm=langchain_community.llms.Cohere(model="cohere.command-light-text-v14"),
            prompt="What is the secret Krabby Patty recipe?",
            mock_tracer=mock_tracer,
            cassette_name="cohere_completion_sync.yaml",
        )
        assert mock_llmobs_span_writer.enqueue.call_count == 1
        _assert_expected_llmobs_llm_span(span, mock_llmobs_span_writer)

    def test_llmobs_ai21_llm(self, langchain_community, mock_llmobs_span_writer, mock_tracer):
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
