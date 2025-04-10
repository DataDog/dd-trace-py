import json
from operator import itemgetter
import os
import sys

import langchain as langchain_
import mock
import pinecone as pinecone_
import pytest

from ddtrace import patch
from ddtrace.internal.utils.version import parse_version
from ddtrace.llmobs import LLMObs
from tests.contrib.langchain.utils import get_request_vcr
from tests.contrib.langchain.utils import long_input_text
from tests.llmobs._utils import _expected_llmobs_llm_span_event
from tests.llmobs._utils import _expected_llmobs_non_llm_span_event
from tests.subprocesstest import SubprocessTestCase
from tests.subprocesstest import run_in_subprocess
from tests.utils import flaky


PINECONE_VERSION = parse_version(pinecone_.__version__)
PY39 = sys.version_info < (3, 10)

from langchain_core.messages import AIMessage
from langchain_core.messages import ChatMessage
from langchain_core.messages import HumanMessage


def _assert_expected_llmobs_llm_span(
    span, mock_llmobs_span_writer, input_role=None, mock_io=False, mock_token_metrics=False, span_links=False
):
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

    metrics = (
        {"input_tokens": mock.ANY, "output_tokens": mock.ANY, "total_tokens": mock.ANY} if mock_token_metrics else {}
    )

    mock_llmobs_span_writer.enqueue.assert_any_call(
        _expected_llmobs_llm_span_event(
            span,
            model_name=span.get_tag("langchain.request.model"),
            model_provider=span.get_tag("langchain.request.provider"),
            input_messages=input_messages if not mock_io else mock.ANY,
            output_messages=output_messages if not mock_io else mock.ANY,
            metadata=metadata,
            token_metrics=metrics,
            tags={"ml_app": "langchain_test", "service": "tests.contrib.langchain"},
            span_links=span_links,
        )
    )


def _assert_expected_llmobs_chain_span(span, mock_llmobs_span_writer, input_value=None, output_value=None, span_links=False):
    expected_chain_span_event = _expected_llmobs_non_llm_span_event(
        span,
        "workflow",
        input_value=input_value if input_value is not None else mock.ANY,
        output_value=output_value if output_value is not None else mock.ANY,
        tags={"ml_app": "langchain_test", "service": "tests.contrib.langchain"},
        span_links=span_links,
    )
    mock_llmobs_span_writer.enqueue.assert_any_call(expected_chain_span_event)


class BaseTestLLMObsLangchain:
    ml_app = "langchain_test"

    @classmethod
    def _invoke_llm(cls, llm, prompt, mock_tracer, cassette_name):
        LLMObs.enable(ml_app=cls.ml_app, integrations_enabled=False, _tracer=mock_tracer)
        if cassette_name is not None:
            with get_request_vcr().use_cassette(cassette_name):
                llm.invoke(prompt)
        else:  # streams do not use casettes
            for _ in llm.stream(prompt):
                pass
        LLMObs.disable()
        return mock_tracer.pop_traces()[0][0]

    @classmethod
    def _invoke_chat(cls, chat_model, prompt, mock_tracer, cassette_name, role="user"):
        LLMObs.enable(ml_app=cls.ml_app, integrations_enabled=False, _tracer=mock_tracer)
        if cassette_name is not None:
            with get_request_vcr().use_cassette(cassette_name):
                if role == "user":
                    messages = [HumanMessage(content=prompt)]
                else:
                    messages = [ChatMessage(content=prompt, role="custom")]
                chat_model.invoke(messages)
        else:  # streams do not use casettes
            for _ in chat_model.stream(prompt):
                pass
        LLMObs.disable()
        return mock_tracer.pop_traces()[0][0]

    @classmethod
    def _invoke_chain(cls, chain, prompt, mock_tracer, cassette_name, batch=False):
        LLMObs.enable(ml_app=cls.ml_app, integrations_enabled=False, _tracer=mock_tracer)
        if cassette_name is not None:
            with get_request_vcr().use_cassette(cassette_name):
                if batch:
                    chain.batch(inputs=prompt)
                else:
                    chain.invoke(prompt)

        else:  # streams do not use casettes
            for _ in chain.stream(prompt):
                pass
        LLMObs.disable()
        return mock_tracer.pop_traces()[0]

    def _embed_query(cls, embedding_model, query, mock_tracer, cassette_name):
        LLMObs.enable(ml_app=cls.ml_app, integrations_enabled=False, _tracer=mock_tracer)
        if cassette_name is not None:
            with get_request_vcr().use_cassette(cassette_name):
                embedding_model.embed_query(query)
        else:  # FakeEmbeddings does not need a cassette
            embedding_model.embed_query(query)
        LLMObs.disable()
        return mock_tracer.pop_traces()[0]

    def _embed_documents(cls, embedding_model, documents, mock_tracer, cassette_name):
        LLMObs.enable(ml_app=cls.ml_app, integrations_enabled=False, _tracer=mock_tracer)
        if cassette_name is not None:
            with get_request_vcr().use_cassette(cassette_name):
                embedding_model.embed_documents(documents)
        else:  # FakeEmbeddings does not need a cassette
            embedding_model.embed_documents(documents)
        LLMObs.disable()
        return mock_tracer.pop_traces()[0]

    @classmethod
    def _similarity_search(cls, pinecone, pinecone_vector_store, embedding_model, query, k, mock_tracer, cassette_name):
        LLMObs.enable(ml_app=cls.ml_app, integrations_enabled=False, _tracer=mock_tracer)
        with get_request_vcr().use_cassette(cassette_name):
            if PINECONE_VERSION <= (2, 2, 4):
                pinecone.init(
                    api_key=os.getenv("PINECONE_API_KEY", "<not-a-real-key>"),
                    environment=os.getenv("PINECONE_ENV", "<not-a-real-env>"),
                )
                index = pinecone.Index(index_name="langchain-retrieval")
            else:
                # Pinecone 2.2.5+ moved init and other methods to a Pinecone class instance
                pc = pinecone.Pinecone(
                    api_key=os.getenv("PINECONE_API_KEY", "<not-a-real-key>"),
                )
                index = pc.Index(name="langchain-retrieval")

            vector_db = pinecone_vector_store(index, embedding_model, "text")
            vector_db.similarity_search(query, k)

        LLMObs.disable()
        return mock_tracer.pop_traces()[0]

    @classmethod
    def _invoke_tool(cls, tool, tool_input, config, mock_tracer):
        LLMObs.enable(ml_app=cls.ml_app, integrations_enabled=False, _tracer=mock_tracer)
        tool.invoke(tool_input, config=config)
        LLMObs.disable()
        return mock_tracer.pop_traces()[0][0]

class TestLLMObsLangchain(BaseTestLLMObsLangchain):
    def test_llmobs_openai_llm(self, langchain_openai, mock_llmobs_span_writer, mock_tracer):
        span = self._invoke_llm(
            llm=langchain_openai.OpenAI(),
            prompt="Can you explain what Descartes meant by 'I think, therefore I am'?",
            mock_tracer=mock_tracer,
            cassette_name="openai_completion_sync.yaml",
        )
        assert mock_llmobs_span_writer.enqueue.call_count == 1
        _assert_expected_llmobs_llm_span(
            span,
            mock_llmobs_span_writer,
            mock_token_metrics=True,
        )

    def test_llmobs_cohere_llm(self, langchain_community, mock_llmobs_span_writer, mock_tracer):
        if langchain_community is None:
            pytest.skip("langchain-community not installed which is required for this test.")
        span = self._invoke_llm(
            llm=langchain_community.llms.Cohere(model="command"),
            prompt="What is the secret Krabby Patty recipe?",
            mock_tracer=mock_tracer,
            cassette_name="cohere_completion_sync.yaml",
        )
        assert mock_llmobs_span_writer.enqueue.call_count == 1
        _assert_expected_llmobs_llm_span(span, mock_llmobs_span_writer)

    @pytest.mark.skipif(PY39, reason="Requires unnecessary cassette file for Python 3.9")
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
        _assert_expected_llmobs_llm_span(
            span,
            mock_llmobs_span_writer,
            input_role="user",
            mock_token_metrics=True,
        )

    def test_llmobs_chain(self, langchain_core, langchain_openai, mock_llmobs_span_writer, mock_tracer):
        prompt = langchain_core.prompts.ChatPromptTemplate.from_messages(
            [("system", "You are world class technical documentation writer."), ("user", "{input}")]
        )
        chain = prompt | langchain_openai.OpenAI()
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
            span_links=True,
        )
        _assert_expected_llmobs_llm_span(
            trace[1],
            mock_llmobs_span_writer,
            mock_token_metrics=True,
            span_links=True,
        )

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
            span_links=True,
        )
        _assert_expected_llmobs_chain_span(
            trace[1],
            mock_llmobs_span_writer,
            input_value=json.dumps([{"person": "Spongebob Squarepants", "language": "Spanish"}]),
            output_value=mock.ANY,
            span_links=True,
        )
        _assert_expected_llmobs_llm_span(
            trace[2],
            mock_llmobs_span_writer,
            input_role="user",
            mock_token_metrics=True,
            span_links=True,
        )
        _assert_expected_llmobs_llm_span(
            trace[3],
            mock_llmobs_span_writer,
            input_role="user",
            mock_token_metrics=True,
            span_links=True,
        )

    @flaky(1735812000, reason="batch() is non-deterministic in which order it processes inputs")
    @pytest.mark.skipif(sys.version_info >= (3, 11), reason="Python <3.11 required")
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
            span_links=True,
        )
        _assert_expected_llmobs_llm_span(
            trace[1],
            mock_llmobs_span_writer,
            input_role="user",
            mock_token_metrics=True,
            span_links=True,
        )
        _assert_expected_llmobs_llm_span(
            trace[2],
            mock_llmobs_span_writer,
            input_role="user",
            mock_token_metrics=True,
            span_links=True,
        )

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
            span_links=True,
        )
        _assert_expected_llmobs_llm_span(
            trace[1],
            mock_llmobs_span_writer,
            mock_io=True,
            mock_token_metrics=True,
            span_links=True,
        )

    def test_llmobs_anthropic_chat_model(self, langchain_anthropic, mock_llmobs_span_writer, mock_tracer):
        chat = langchain_anthropic.ChatAnthropic(temperature=0, model="claude-3-opus-20240229", max_tokens=15)
        span = self._invoke_chat(
            chat_model=chat,
            prompt="When do you use 'whom' instead of 'who'?",
            mock_tracer=mock_tracer,
            cassette_name="anthropic_chat_completion_sync.yaml",
        )
        assert mock_llmobs_span_writer.enqueue.call_count == 1
        _assert_expected_llmobs_llm_span(
            span,
            mock_llmobs_span_writer,
            input_role="user",
            mock_token_metrics=True,
        )

    def test_llmobs_embedding_query(self, langchain_community, langchain_openai, mock_llmobs_span_writer, mock_tracer):
        if langchain_openai is None:
            pytest.skip("langchain_openai not installed which is required for this test.")
        embedding_model = langchain_openai.embeddings.OpenAIEmbeddings()
        with mock.patch("langchain_openai.OpenAIEmbeddings._get_len_safe_embeddings", return_value=[0.0] * 1536):
            trace = self._embed_query(
                embedding_model=embedding_model,
                query="hello world",
                mock_tracer=mock_tracer,
                cassette_name="openai_embedding_query.yaml",
            )
        assert mock_llmobs_span_writer.enqueue.call_count == 1
        span = trace[0] if isinstance(trace, list) else trace
        mock_llmobs_span_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                span_kind="embedding",
                model_name=embedding_model.model,
                model_provider="openai",
                input_documents=[{"text": "hello world"}],
                output_value="[1 embedding(s) returned with size 1536]",
                tags={"ml_app": "langchain_test", "service": "tests.contrib.langchain"},
            )
        )

    def test_llmobs_embedding_documents(
        self, langchain_community, langchain_openai, mock_llmobs_span_writer, mock_tracer
    ):
        if langchain_community is None:
            pytest.skip("langchain-community not installed which is required for this test.")
        embedding_model = langchain_community.embeddings.FakeEmbeddings(size=1536)
        trace = self._embed_documents(
            embedding_model=embedding_model,
            documents=["hello world", "goodbye world"],
            mock_tracer=mock_tracer,
            cassette_name=None,  # FakeEmbeddings does not need a cassette
        )
        assert mock_llmobs_span_writer.enqueue.call_count == 1
        span = trace[0] if isinstance(trace, list) else trace
        mock_llmobs_span_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                span_kind="embedding",
                model_name="",
                model_provider="fake",
                input_documents=[{"text": "hello world"}, {"text": "goodbye world"}],
                output_value="[2 embedding(s) returned with size 1536]",
                tags={"ml_app": "langchain_test", "service": "tests.contrib.langchain"},
            )
        )

    def test_llmobs_similarity_search(self, langchain_openai, langchain_pinecone, mock_llmobs_span_writer, mock_tracer):
        import pinecone

        if langchain_pinecone is None:
            pytest.skip("langchain_pinecone not installed which is required for this test.")
        embedding_model = langchain_openai.OpenAIEmbeddings(model="text-embedding-ada-002")
        cassette_name = "openai_pinecone_similarity_search_community.yaml"
        with mock.patch("langchain_openai.OpenAIEmbeddings._get_len_safe_embeddings", return_value=[[0.0] * 1536]):
            trace = self._similarity_search(
                pinecone=pinecone,
                pinecone_vector_store=langchain_pinecone.PineconeVectorStore,
                embedding_model=embedding_model,
                query="Evolution",
                k=1,
                mock_tracer=mock_tracer,
                cassette_name=cassette_name,
            )
        assert mock_llmobs_span_writer.enqueue.call_count == 2
        expected_span = _expected_llmobs_non_llm_span_event(
            trace[0],
            "retrieval",
            input_value="Evolution",
            output_documents=[
                {"text": mock.ANY, "id": mock.ANY, "name": "The Evolution of Communication Technologies"}
            ],
            output_value="[1 document(s) retrieved]",
            tags={"ml_app": "langchain_test", "service": "tests.contrib.langchain"},
        )
        mock_llmobs_span_writer.enqueue.assert_any_call(expected_span)

    def test_llmobs_chat_model_tool_calls(self, langchain_openai, mock_llmobs_span_writer, mock_tracer):
        import langchain_core.tools

        @langchain_core.tools.tool
        def add(a: int, b: int) -> int:
            """Adds a and b.
            Args:
                a: first int
                b: second int
            """
            return a + b

        llm = langchain_openai.ChatOpenAI(model="gpt-3.5-turbo-0125")
        llm_with_tools = llm.bind_tools([add])
        span = self._invoke_chat(
            chat_model=llm_with_tools,
            prompt="What is the sum of 1 and 2?",
            mock_tracer=mock_tracer,
            cassette_name="lcel_with_tools_openai.yaml",
        )
        assert mock_llmobs_span_writer.enqueue.call_count == 1
        mock_llmobs_span_writer.enqueue.assert_any_call(
            _expected_llmobs_llm_span_event(
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
        )

    def test_llmobs_base_tool_invoke(self, langchain_core, mock_llmobs_span_writer, mock_tracer):
        if langchain_core is None:
            pytest.skip("langchain-core not installed which is required for this test.")

        from math import pi

        from langchain_core.tools import StructuredTool

        def circumference_tool(radius: float) -> float:
            return float(radius) * 2.0 * pi

        calculator = StructuredTool.from_function(
            func=circumference_tool,
            name="Circumference calculator",
            description="Use this tool when you need to calculate a circumference using the radius of a circle",
            return_direct=True,
            response_format="content",
        )

        span = self._invoke_tool(
            tool=calculator,
            tool_input="2",
            config={"test": "this is to test config"},
            mock_tracer=mock_tracer,
        )
        assert mock_llmobs_span_writer.enqueue.call_count == 1
        mock_llmobs_span_writer.enqueue.assert_called_with(
            _expected_llmobs_non_llm_span_event(
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
        )

    def test_llmobs_streamed_chain(
        self, langchain_core, langchain_openai, mock_llmobs_span_writer, mock_tracer, streamed_response_responder
    ):
        client = streamed_response_responder(
            module="openai",
            client_class_key="OpenAI",
            http_client_key="http_client",
            endpoint_path=["chat", "completions"],
            file="lcel_openai_chat_streamed_response.txt",
        )

        prompt = langchain_core.prompts.ChatPromptTemplate.from_messages(
            [("system", "You are a world class technical documentation writer."), ("user", "{input}")]
        )
        llm = langchain_openai.ChatOpenAI(model="gpt-4o", client=client)
        parser = langchain_core.output_parsers.StrOutputParser()

        chain = prompt | llm | parser

        trace = self._invoke_chain(
            chain=chain,
            prompt={"input": "how can langsmith help with testing?"},
            mock_tracer=mock_tracer,
            cassette_name=None,  # do not use cassette,
        )

        assert mock_llmobs_span_writer.enqueue.call_count == 2
        _assert_expected_llmobs_chain_span(
            trace[0],
            mock_llmobs_span_writer,
            input_value=json.dumps({"input": "how can langsmith help with testing?"}),
            output_value="Python is\n\nthe best!",
            span_links=True,
        )
        mock_llmobs_span_writer.enqueue.assert_any_call(
            _expected_llmobs_llm_span_event(
                trace[1],
                model_name=trace[1].get_tag("langchain.request.model"),
                model_provider=trace[1].get_tag("langchain.request.provider"),
                input_messages=[
                    {"content": "You are a world class technical documentation writer.", "role": "SystemMessage"},
                    {"content": "how can langsmith help with testing?", "role": "HumanMessage"},
                ],
                output_messages=[{"content": "Python is\n\nthe best!", "role": "assistant"}],
                metadata={"temperature": 0.7},
                token_metrics={},
                tags={"ml_app": "langchain_test", "service": "tests.contrib.langchain"},
                span_links=True,
            )
        )

    def test_llmobs_streamed_llm(
        self, langchain_openai, mock_llmobs_span_writer, mock_tracer, streamed_response_responder
    ):
        client = streamed_response_responder(
            module="openai",
            client_class_key="OpenAI",
            http_client_key="http_client",
            endpoint_path=["completions"],
            file="lcel_openai_llm_streamed_response.txt",
        )

        llm = langchain_openai.OpenAI(client=client)

        span = self._invoke_llm(
            cassette_name=None,  # do not use cassette
            llm=llm,
            mock_tracer=mock_tracer,
            prompt="Hello!",
        )

        assert mock_llmobs_span_writer.enqueue.call_count == 1
        mock_llmobs_span_writer.enqueue.assert_any_call(
            _expected_llmobs_llm_span_event(
                span,
                model_name=span.get_tag("langchain.request.model"),
                model_provider=span.get_tag("langchain.request.provider"),
                input_messages=[
                    {"content": "Hello!"},
                ],
                output_messages=[{"content": "\n\nPython is cool!"}],
                metadata={"temperature": 0.7, "max_tokens": 256},
                token_metrics={},
                tags={"ml_app": "langchain_test", "service": "tests.contrib.langchain"},
            )
        )

    def test_llmobs_non_ascii_completion(self, langchain_openai, mock_llmobs_span_writer, mock_tracer):
        self._invoke_llm(
            llm=langchain_openai.OpenAI(),
            prompt="안녕,\n 지금 몇 시야?",
            mock_tracer=mock_tracer,
            cassette_name="openai_completion_non_ascii.yaml",
        )
        assert mock_llmobs_span_writer.enqueue.call_count == 1
        actual_llmobs_span_event = mock_llmobs_span_writer.enqueue.call_args[0][0]
        assert actual_llmobs_span_event["meta"]["input"]["messages"][0]["content"] == "안녕,\n 지금 몇 시야?"


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

        super(TestTraceStructureWithLLMIntegrations, self).setUp()

    def tearDown(self):
        LLMObs.disable()

    def _assert_trace_structure_from_writer_call_args(self, span_kinds):
        assert self.mock_llmobs_span_writer.enqueue.call_count == len(span_kinds)

        calls = self.mock_llmobs_span_writer.enqueue.call_args_list[::-1]

        for span_kind, call in zip(span_kinds, calls):
            call_args = call.args[0]

            assert call_args["meta"]["span.kind"] == span_kind, f"Span kind is {call_args['meta']['span.kind']} but expected {span_kind}"
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
    def _call_bedrock_chat_model(ChatBedrock, HumanMessage):
        chat = ChatBedrock(
            model_id="amazon.titan-tg1-large",
            model_kwargs={"max_tokens": 50, "temperature": 0},
        )
        messages = [HumanMessage(content="summarize the plot to the lord of the rings in a dozen words")]
        with get_request_vcr().use_cassette("bedrock_amazon_chat_invoke.yaml"):
            chat.invoke(messages)

    @staticmethod
    def _call_bedrock_llm(BedrockLLM):
        llm = BedrockLLM(
            model_id="amazon.titan-tg1-large",
            region_name="us-east-1",
            model_kwargs={"temperature": 0, "topP": 0.9, "stopSequences": [], "maxTokens": 50},
        )

        with get_request_vcr().use_cassette("bedrock_amazon_invoke.yaml"):
            llm.invoke("can you explain what Datadog is to someone not in the tech industry?")

    @staticmethod
    def _call_openai_llm(OpenAI):
        llm = OpenAI()
        with get_request_vcr().use_cassette("openai_completion_sync.yaml"):
            llm.invoke("Can you explain what Descartes meant by 'I think, therefore I am'?")

    @staticmethod
    def _call_openai_embedding(OpenAIEmbeddings):
        embedding = OpenAIEmbeddings()
        with mock.patch("langchain_openai.embeddings.base.tiktoken.encoding_for_model") as mock_encoding_for_model:
            mock_encoding = mock.MagicMock()
            mock_encoding_for_model.return_value = mock_encoding
            mock_encoding.encode.return_value = [0.0] * 1536
            with get_request_vcr().use_cassette(
                "openai_embedding_query_integration.yaml"
            ):
                embedding.embed_query("hello world")

    @staticmethod
    def _call_anthropic_chat(Anthropic):
        llm = Anthropic(model="claude-3-opus-20240229", max_tokens=15)
        with get_request_vcr().use_cassette(
            "anthropic_chat_completion_sync.yaml"
        ):
            llm.invoke("When do you use 'whom' instead of 'who'?")

    @run_in_subprocess(env_overrides=bedrock_env_config)
    def test_llmobs_with_chat_model_bedrock_enabled(self):
        from langchain_aws import ChatBedrock
        from langchain_core.messages import HumanMessage

        patch(langchain=True, botocore=True)
        LLMObs.enable(ml_app="<ml-app-name>", integrations_enabled=False)

        self._call_bedrock_chat_model(ChatBedrock, HumanMessage)

        self._assert_trace_structure_from_writer_call_args(["workflow", "llm"])

    @run_in_subprocess(env_overrides=bedrock_env_config)
    def test_llmobs_with_chat_model_bedrock_disabled(self):
        from langchain_aws import ChatBedrock
        from langchain_core.messages import HumanMessage

        patch(langchain=True)
        LLMObs.enable(ml_app="<ml-app-name>", integrations_enabled=False)

        self._call_bedrock_chat_model(ChatBedrock, HumanMessage)

        self._assert_trace_structure_from_writer_call_args(["llm"])

    @run_in_subprocess(env_overrides=bedrock_env_config)
    def test_llmobs_with_llm_model_bedrock_enabled(self):
        from langchain_aws import BedrockLLM

        patch(langchain=True, botocore=True)
        LLMObs.enable(ml_app="<ml-app-name>", integrations_enabled=False)
        self._call_bedrock_llm(BedrockLLM)
        self._assert_trace_structure_from_writer_call_args(["workflow", "llm"])

    @run_in_subprocess(env_overrides=bedrock_env_config)
    def test_llmobs_with_llm_model_bedrock_disabled(self):
        from langchain_aws import BedrockLLM

        patch(langchain=True)
        LLMObs.enable(ml_app="<ml-app-name>", integrations_enabled=False)
        self._call_bedrock_llm(BedrockLLM)
        self._assert_trace_structure_from_writer_call_args(["llm"])

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
        llm = OpenAI()
        with get_request_vcr().use_cassette("openai_completion_non_ascii.yaml"):
            llm.invoke("안녕,\n 지금 몇 시야?")
        langchain_span = self.mock_llmobs_span_writer.enqueue.call_args_list[0][0][0]
        assert langchain_span["meta"]["input"]["value"] == '[{"content": "안녕,\\n 지금 몇 시야?"}]'

    @run_in_subprocess(env_overrides=openai_env_config)
    def test_llmobs_with_openai_disabled(self):
        from langchain_openai import OpenAI

        patch(langchain=True)

        LLMObs.enable(ml_app="<ml-app-name>", integrations_enabled=False)
        self._call_openai_llm(OpenAI)
        self._assert_trace_structure_from_writer_call_args(["llm"])

    @run_in_subprocess(env_overrides=openai_env_config)
    def test_llmobs_langchain_with_embedding_model_openai_enabled(self):
        from langchain_openai import OpenAIEmbeddings

        patch(langchain=True, openai=True)
        LLMObs.enable(ml_app="<ml-app-name>", integrations_enabled=False)
        self._call_openai_embedding(OpenAIEmbeddings)
        self._assert_trace_structure_from_writer_call_args(["workflow", "embedding"])

    @run_in_subprocess(env_overrides=openai_env_config)
    def test_llmobs_langchain_with_embedding_model_openai_disabled(self):
        from langchain_openai import OpenAIEmbeddings

        patch(langchain=True)
        LLMObs.enable(ml_app="<ml-app-name>", integrations_enabled=False)
        self._call_openai_embedding(OpenAIEmbeddings)
        self._assert_trace_structure_from_writer_call_args(["embedding"])

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