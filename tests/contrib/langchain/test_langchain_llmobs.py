import os
import sys

import langchain as langchain_
import mock
import pinecone as pinecone_

from ddtrace.internal.utils.version import parse_version
from ddtrace.llmobs import LLMObs
from tests.contrib.langchain.utils import get_request_vcr
from tests.llmobs._utils import _expected_llmobs_llm_span_event
from tests.llmobs._utils import _expected_llmobs_non_llm_span_event


LANGCHAIN_VERSION = parse_version(langchain_.__version__)
PINECONE_VERSION = parse_version(pinecone_.__version__)
PY39 = sys.version_info < (3, 10)

if LANGCHAIN_VERSION < (0, 1):
    from langchain.schema import ChatMessage
    from langchain.schema import HumanMessage
else:
    from langchain_core.messages import ChatMessage
    from langchain_core.messages import HumanMessage


def _assert_expected_llmobs_llm_span(
    span, mock_llmobs_span_writer, input_role=None, mock_io=False, mock_token_metrics=False
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
        )
    )


def _assert_expected_llmobs_chain_span(span, mock_llmobs_span_writer, input_value=None, output_value=None):
    expected_chain_span_event = _expected_llmobs_non_llm_span_event(
        span,
        "workflow",
        input_value=input_value if input_value is not None else mock.ANY,
        output_value=output_value if output_value is not None else mock.ANY,
        tags={"ml_app": "langchain_test", "service": "tests.contrib.langchain"},
    )
    mock_llmobs_span_writer.enqueue.assert_any_call(expected_chain_span_event)


class BaseTestLLMObsLangchain:
    cassette_subdirectory_name = "langchain"
    ml_app = "langchain_test"

    @classmethod
    def _invoke_llm(cls, llm, prompt, mock_tracer, cassette_name):
        LLMObs.enable(ml_app=cls.ml_app, integrations_enabled=False, _tracer=mock_tracer)
        if cassette_name is not None:
            with get_request_vcr(subdirectory_name=cls.cassette_subdirectory_name).use_cassette(cassette_name):
                if LANGCHAIN_VERSION < (0, 1):
                    llm(prompt)
                else:
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
            with get_request_vcr(subdirectory_name=cls.cassette_subdirectory_name).use_cassette(cassette_name):
                if role == "user":
                    messages = [HumanMessage(content=prompt)]
                else:
                    messages = [ChatMessage(content=prompt, role="custom")]
                if LANGCHAIN_VERSION < (0, 1):
                    chat_model(messages)
                else:
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
            with get_request_vcr(subdirectory_name=cls.cassette_subdirectory_name).use_cassette(cassette_name):
                if batch:
                    chain.batch(inputs=prompt)
                elif LANGCHAIN_VERSION < (0, 1):
                    chain.run(prompt)
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
            with get_request_vcr(subdirectory_name=cls.cassette_subdirectory_name).use_cassette(cassette_name):
                embedding_model.embed_query(query)
        else:  # FakeEmbeddings does not need a cassette
            embedding_model.embed_query(query)
        LLMObs.disable()
        return mock_tracer.pop_traces()[0]

    def _embed_documents(cls, embedding_model, documents, mock_tracer, cassette_name):
        LLMObs.enable(ml_app=cls.ml_app, integrations_enabled=False, _tracer=mock_tracer)
        if cassette_name is not None:
            with get_request_vcr(subdirectory_name=cls.cassette_subdirectory_name).use_cassette(cassette_name):
                embedding_model.embed_documents(documents)
        else:  # FakeEmbeddings does not need a cassette
            embedding_model.embed_documents(documents)
        LLMObs.disable()
        return mock_tracer.pop_traces()[0]

    @classmethod
    def _similarity_search(cls, pinecone, pinecone_vector_store, embedding_model, query, k, mock_tracer, cassette_name):
        LLMObs.enable(ml_app=cls.ml_app, integrations_enabled=False, _tracer=mock_tracer)
        with get_request_vcr(subdirectory_name=cls.cassette_subdirectory_name).use_cassette(cassette_name):
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
        if LANGCHAIN_VERSION > (0, 1):
            tool.invoke(tool_input, config=config)
        LLMObs.disable()
        return mock_tracer.pop_traces()[0][0]
