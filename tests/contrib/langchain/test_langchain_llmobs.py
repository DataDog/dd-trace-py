import json
from operator import itemgetter
import os
import sys

from langchain_core.messages import AIMessage
from langchain_core.messages import HumanMessage
import mock
import pinecone as pinecone_
import pytest

from ddtrace import patch
from ddtrace.internal.utils.version import parse_version
from ddtrace.llmobs import LLMObs
from tests.contrib.langchain.utils import get_request_vcr
from tests.llmobs._utils import _expected_llmobs_llm_span_event
from tests.llmobs._utils import _expected_llmobs_non_llm_span_event
from tests.subprocesstest import SubprocessTestCase
from tests.subprocesstest import run_in_subprocess


PINECONE_VERSION = parse_version(pinecone_.__version__)


def _expected_langchain_llmobs_llm_span(
    span, input_role=None, mock_io=False, mock_token_metrics=False, span_links=False
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

    return _expected_llmobs_llm_span_event(
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


def _expected_langchain_llmobs_chain_span(span, input_value=None, output_value=None, span_links=False):
    return _expected_llmobs_non_llm_span_event(
        span,
        "workflow",
        input_value=input_value if input_value is not None else mock.ANY,
        output_value=output_value if output_value is not None else mock.ANY,
        tags={"ml_app": "langchain_test", "service": "tests.contrib.langchain"},
        span_links=span_links,
    )


def test_llmobs_openai_llm(langchain_openai, llmobs_events, tracer, openai_completion):
    llm = langchain_openai.OpenAI()
    llm.invoke("Can you explain what Descartes meant by 'I think, therefore I am'?")

    span = tracer.pop_traces()[0][0]
    assert len(llmobs_events) == 1
    assert llmobs_events[0] == _expected_langchain_llmobs_llm_span(span, mock_token_metrics=True)


def test_llmobs_openai_chat_model(langchain_openai, llmobs_events, tracer, openai_chat_completion):
    chat_model = langchain_openai.ChatOpenAI(temperature=0, max_tokens=256)
    chat_model.invoke([HumanMessage(content="When do you use 'who' instead of 'whom'?")])

    span = tracer.pop_traces()[0][0]
    assert len(llmobs_events) == 1
    assert llmobs_events[0] == _expected_langchain_llmobs_llm_span(
        span,
        input_role="user",
        mock_token_metrics=True,
    )


def test_llmobs_chain(langchain_core, langchain_openai, llmobs_events, tracer):
    prompt = langchain_core.prompts.ChatPromptTemplate.from_messages(
        [("system", "You are world class technical documentation writer."), ("user", "{input}")]
    )
    chain = prompt | langchain_openai.OpenAI()
    with get_request_vcr().use_cassette("lcel_openai_chain_call.yaml"):
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
    )


def test_llmobs_chain_nested(langchain_core, langchain_openai, llmobs_events, tracer):
    prompt1 = langchain_core.prompts.ChatPromptTemplate.from_template("what is the city {person} is from?")
    prompt2 = langchain_core.prompts.ChatPromptTemplate.from_template(
        "what country is the city {city} in? respond in {language}"
    )
    model = langchain_openai.ChatOpenAI()
    chain1 = prompt1 | model | langchain_core.output_parsers.StrOutputParser()
    chain2 = prompt2 | model | langchain_core.output_parsers.StrOutputParser()
    complete_chain = {"city": chain1, "language": itemgetter("language")} | chain2

    with get_request_vcr().use_cassette("lcel_openai_chain_nested.yaml"):
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
        input_role="user",
        mock_token_metrics=True,
        span_links=True,
    )
    assert llmobs_events[3] == _expected_langchain_llmobs_llm_span(
        trace[3],
        input_role="user",
        mock_token_metrics=True,
        span_links=True,
    )


@pytest.mark.skipif(sys.version_info >= (3, 11), reason="Python <3.11 required")
def test_llmobs_chain_batch(langchain_core, langchain_openai, llmobs_events, tracer):
    prompt = langchain_core.prompts.ChatPromptTemplate.from_template("Tell me a short joke about {topic}")
    output_parser = langchain_core.output_parsers.StrOutputParser()
    model = langchain_openai.ChatOpenAI()
    chain = {"topic": langchain_core.runnables.RunnablePassthrough()} | prompt | model | output_parser

    with get_request_vcr().use_cassette("lcel_openai_chain_batch.yaml"):
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
        )
        assert llmobs_events[2] == _expected_langchain_llmobs_llm_span(
            trace[2],
            input_role="user",
            mock_token_metrics=True,
            span_links=True,
        )
    except AssertionError:
        assert llmobs_events[1] == _expected_langchain_llmobs_llm_span(
            trace[2],
            input_role="user",
            mock_token_metrics=True,
            span_links=True,
        )
        assert llmobs_events[2] == _expected_langchain_llmobs_llm_span(
            trace[1],
            input_role="user",
            mock_token_metrics=True,
            span_links=True,
        )


def test_llmobs_chain_schema_io(langchain_core, langchain_openai, llmobs_events, tracer):
    prompt = langchain_core.prompts.ChatPromptTemplate.from_messages(
        [
            ("system", "You're an assistant who's good at {ability}. Respond in 20 words or fewer"),
            langchain_core.prompts.MessagesPlaceholder(variable_name="history"),
            ("human", "{input}"),
        ]
    )
    chain = prompt | langchain_openai.ChatOpenAI()

    with get_request_vcr().use_cassette("lcel_openai_chain_schema_io.yaml"):
        chain.invoke(
            {
                "ability": "world capitals",
                "history": [
                    HumanMessage(content="Can you be my science teacher instead?"),
                    AIMessage(content="Yes"),
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
        output_value=json.dumps(["assistant", "Mitochondria."]),
        span_links=True,
    )
    assert llmobs_events[1] == _expected_langchain_llmobs_llm_span(
        trace[1],
        mock_io=True,
        mock_token_metrics=True,
        span_links=True,
    )


def test_llmobs_anthropic_chat_model(langchain_anthropic, llmobs_events, tracer):
    chat = langchain_anthropic.ChatAnthropic(temperature=0, model="claude-3-opus-20240229", max_tokens=15)
    with get_request_vcr().use_cassette("anthropic_chat_completion_sync.yaml"):
        chat.invoke("When do you use 'whom' instead of 'who'?")

    span = tracer.pop_traces()[0][0]
    assert len(llmobs_events) == 1
    assert llmobs_events[0] == _expected_langchain_llmobs_llm_span(
        span,
        input_role="user",
        mock_token_metrics=True,
    )


def test_llmobs_embedding_query(langchain_openai, llmobs_events, tracer):
    if langchain_openai is None:
        pytest.skip("langchain_openai not installed which is required for this test.")
    embedding_model = langchain_openai.embeddings.OpenAIEmbeddings()
    with mock.patch("langchain_openai.OpenAIEmbeddings._get_len_safe_embeddings", return_value=[0.0] * 1536):
        with get_request_vcr().use_cassette("openai_embedding_query.yaml"):
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


def test_llmobs_embedding_documents(langchain_community, llmobs_events, tracer):
    if langchain_community is None:
        pytest.skip("langchain-community not installed which is required for this test.")
    embedding_model = langchain_community.embeddings.FakeEmbeddings(size=1536)
    embedding_model.embed_documents(["hello world", "goodbye world"])

    trace = tracer.pop_traces()[0]
    span = trace[0] if isinstance(trace, list) else trace
    assert len(llmobs_events) == 1
    assert llmobs_events[0] == _expected_llmobs_llm_span_event(
        span,
        span_kind="embedding",
        model_name="",
        model_provider="fake",
        input_documents=[{"text": "hello world"}, {"text": "goodbye world"}],
        output_value="[2 embedding(s) returned with size 1536]",
        tags={"ml_app": "langchain_test", "service": "tests.contrib.langchain"},
    )


def test_llmobs_similarity_search(langchain_openai, langchain_pinecone, llmobs_events, tracer):
    import pinecone

    if langchain_pinecone is None:
        pytest.skip("langchain_pinecone not installed which is required for this test.")
    embedding_model = langchain_openai.OpenAIEmbeddings(model="text-embedding-ada-002")
    with mock.patch("langchain_openai.OpenAIEmbeddings._get_len_safe_embeddings", return_value=[[0.0] * 1536]):
        with get_request_vcr().use_cassette("openai_pinecone_similarity_search.yaml"):
            if PINECONE_VERSION <= (2, 2, 4):
                pinecone.init(
                    api_key=os.getenv("PINECONE_API_KEY", "<not-a-real-key>"),
                    environment=os.getenv("PINECONE_ENV", "<not-a-real-env>"),
                )
                index = pinecone.Index(index_name="langchain-retrieval")
            else:
                pc = pinecone.Pinecone(
                    api_key=os.getenv("PINECONE_API_KEY", "<not-a-real-key>"),
                )
                index = pc.Index(name="langchain-retrieval")
            vector_db = langchain_pinecone.PineconeVectorStore(index, embedding_model, "text")
            vector_db.similarity_search("Evolution", 1)

    trace = tracer.pop_traces()[0]
    assert len(llmobs_events) == 2
    llmobs_events.sort(key=lambda span: span["start_ns"])
    expected_span = _expected_llmobs_non_llm_span_event(
        trace[0],
        "retrieval",
        input_value="Evolution",
        output_documents=[{"text": mock.ANY, "id": mock.ANY, "name": mock.ANY}],
        output_value="[1 document(s) retrieved]",
        tags={"ml_app": "langchain_test", "service": "tests.contrib.langchain"},
        span_links=True,
    )
    assert llmobs_events[0] == expected_span


def test_llmobs_chat_model_tool_calls(langchain_openai, llmobs_events, tracer, openai_chat_completion_tools):
    import langchain_core.tools

    @langchain_core.tools.tool
    def add(a: int, b: int) -> int:
        """Adds a and b.
        Args:
            a: first int
            b: second int
        """
        return a + b

    llm = langchain_openai.ChatOpenAI(model="gpt-3.5-turbo-0125", temperature=0.7)
    llm_with_tools = llm.bind_tools([add])
    llm_with_tools.invoke([HumanMessage(content="What is the sum of 1 and 2?")])

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
                "content": "Hello world!",
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


def test_llmobs_base_tool_invoke(llmobs_events, tracer):
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


def test_llmobs_streamed_chain(langchain_core, langchain_openai, llmobs_events, tracer, streamed_response_responder):
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
    llm = langchain_openai.ChatOpenAI(model="gpt-4o", client=client, temperature=0.7)
    parser = langchain_core.output_parsers.StrOutputParser()

    chain = prompt | llm | parser

    for _ in chain.stream({"input": "how can langsmith help with testing?"}):
        pass

    trace = tracer.pop_traces()[0]
    assert len(llmobs_events) == 2
    llmobs_events.sort(key=lambda span: span["start_ns"])
    assert llmobs_events[0] == _expected_langchain_llmobs_chain_span(
        trace[0],
        input_value=json.dumps({"input": "how can langsmith help with testing?"}),
        output_value="Python is\n\nthe best!",
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
        output_messages=[{"content": "Python is\n\nthe best!", "role": "assistant"}],
        metadata={"temperature": 0.7},
        token_metrics={},
        tags={"ml_app": "langchain_test", "service": "tests.contrib.langchain"},
        span_links=True,
    )


def test_llmobs_streamed_llm(langchain_openai, llmobs_events, tracer, streamed_response_responder):
    client = streamed_response_responder(
        module="openai",
        client_class_key="OpenAI",
        http_client_key="http_client",
        endpoint_path=["completions"],
        file="lcel_openai_llm_streamed_response.txt",
    )

    llm = langchain_openai.OpenAI(client=client)

    for _ in llm.stream("Hello!"):
        pass

    span = tracer.pop_traces()[0][0]
    assert len(llmobs_events) == 1
    assert llmobs_events[0] == _expected_llmobs_llm_span_event(
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


def test_llmobs_non_ascii_completion(langchain_openai, llmobs_events, tracer):
    llm = langchain_openai.OpenAI()
    with get_request_vcr().use_cassette("openai_completion_non_ascii.yaml"):
        llm.invoke("안녕,\n 지금 몇 시야?")

    assert len(llmobs_events) == 1
    actual_llmobs_span_event = llmobs_events[0]
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

            assert (
                call_args["meta"]["span.kind"] == span_kind
            ), f"Span kind is {call_args['meta']['span.kind']} but expected {span_kind}"
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
            model_kwargs={"maxTokenCount": 50, "temperature": 0},
        )
        messages = [HumanMessage(content="summarize the plot to the lord of the rings in a dozen words")]
        with get_request_vcr().use_cassette("bedrock_amazon_chat_invoke.yaml"):
            chat.invoke(messages)

    @staticmethod
    def _call_bedrock_llm(BedrockLLM):
        llm = BedrockLLM(
            model_id="amazon.titan-tg1-large",
            region_name="us-east-1",
            model_kwargs={"temperature": 0, "topP": 0.9, "stopSequences": [], "maxTokenCount": 50},
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
            with get_request_vcr().use_cassette("openai_embedding_query_integration.yaml"):
                embedding.embed_query("hello world")

    @staticmethod
    def _call_anthropic_chat(Anthropic):
        llm = Anthropic(model="claude-3-opus-20240229", max_tokens=15)
        with get_request_vcr().use_cassette("anthropic_chat_completion_sync.yaml"):
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
        langchain_span = self.mock_llmobs_span_writer.enqueue.call_args_list[1][0][0]
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
        import langchain  # noqa: F401
        from langchain_openai import OpenAIEmbeddings

        patch(langchain=True, openai=True)

        LLMObs.enable(ml_app="<ml-app-name>", integrations_enabled=False)
        self._call_openai_embedding(OpenAIEmbeddings)
        self._assert_trace_structure_from_writer_call_args(["workflow", "embedding"])

    @run_in_subprocess(env_overrides=openai_env_config)
    def test_llmobs_langchain_with_embedding_model_openai_disabled(self):
        import langchain  # noqa: F401
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
