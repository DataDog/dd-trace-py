from operator import itemgetter
import os
import sys

import langchain_core
import langchain_core.messages  # noqa: F401
import mock
import pytest

from ddtrace.internal.utils.version import parse_version
from tests.contrib.langchain.utils import get_request_vcr
from tests.utils import flaky
from tests.utils import override_global_config


LANGCHAIN_VERSION = parse_version(langchain_core.__version__)

IGNORE_FIELDS = [
    "resources",
    "meta.openai.request.logprobs",  # langchain-openai llm call now includes logprobs as param
    "meta.error.stack",
    "meta.http.useragent",
    "meta.langchain.request.openai-chat.parameters.logprobs",
    "meta.langchain.request.openai.parameters.logprobs",
    "meta.langchain.request.openai.parameters.seed",  # langchain-openai llm call now includes seed as param
    "meta.langchain.request.openai.parameters.logprobs",  # langchain-openai llm call now includes seed as param
    "metrics.langchain.tokens.total_cost",  # total_cost depends on if tiktoken is installed
]


@pytest.fixture(scope="session")
def request_vcr():
    yield get_request_vcr(subdirectory_name="langchain_community")


@pytest.mark.parametrize("ddtrace_config_langchain", [dict(logs_enabled=True, log_prompt_completion_sample_rate=1.0)])
def test_global_tags(ddtrace_config_langchain, langchain_openai, request_vcr, mock_metrics, mock_logs, mock_tracer):
    """
    When the global config UST tags are set
        The service name should be used for all data
        The env should be used for all data
        The version should be used for all data
    """
    llm = langchain_openai.OpenAI()
    with override_global_config(dict(service="test-svc", env="staging", version="1234")):
        with request_vcr.use_cassette("openai_completion_sync.yaml"):
            llm.invoke("What does Nietzsche mean by 'God is dead'?")

    span = mock_tracer.pop_traces()[0][0]
    assert span.resource == "langchain_openai.llms.base.OpenAI"
    assert span.service == "test-svc"
    assert span.get_tag("env") == "staging"
    assert span.get_tag("version") == "1234"
    assert span.get_tag("langchain.request.provider") == "openai"
    assert span.get_tag("langchain.request.model") == "gpt-3.5-turbo-instruct"
    assert span.get_tag("langchain.request.api_key") == "...key>"

    assert mock_logs.enqueue.call_count == 1
    assert mock_metrics.mock_calls
    for _, _args, kwargs in mock_metrics.mock_calls:
        expected_metrics = [
            "service:test-svc",
            "env:staging",
            "version:1234",
            "langchain.request.model:gpt-3.5-turbo-instruct",
            "langchain.request.provider:openai",
            "langchain.request.type:llm",
            "langchain.request.api_key:...key>",
        ]
        actual_tags = kwargs.get("tags")
        for m in expected_metrics:
            assert m in actual_tags

    for call, args, _kwargs in mock_logs.mock_calls:
        if call != "enqueue":
            continue
        log = args[0]
        assert log["service"] == "test-svc"
        assert (
            log["ddtags"]
            == "env:staging,version:1234,langchain.request.provider:openai,langchain.request.model:gpt-3.5-turbo-instruct,langchain.request.type:llm,langchain.request.api_key:...key>"  # noqa: E501
        )


@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_openai_llm_sync(langchain_openai, request_vcr):
    llm = langchain_openai.OpenAI()
    with request_vcr.use_cassette("openai_completion_sync.yaml"):
        llm.invoke("Can you explain what Descartes meant by 'I think, therefore I am'?")


@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_openai_llm_sync_multiple_prompts(langchain_openai, request_vcr):
    llm = langchain_openai.OpenAI()
    with request_vcr.use_cassette("openai_completion_sync_multi_prompt.yaml"):
        llm.generate(
            prompts=[
                "What is the best way to teach a baby multiple languages?",
                "How many times has Spongebob failed his road test?",
            ]
        )


@pytest.mark.asyncio
@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
async def test_openai_llm_async(langchain_openai, request_vcr):
    llm = langchain_openai.OpenAI()
    with request_vcr.use_cassette("openai_completion_async.yaml"):
        await llm.agenerate(["Which team won the 2019 NBA finals?"])


@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_openai_llm_error(langchain_core, langchain_openai, request_vcr):
    import openai  # Imported here because the os env OPENAI_API_KEY needs to be set via langchain fixture before import

    llm = langchain_openai.OpenAI()

    if parse_version(openai.__version__) >= (1, 0, 0):
        invalid_error = openai.BadRequestError
    else:
        invalid_error = openai.InvalidRequestError
    with pytest.raises(invalid_error):
        with request_vcr.use_cassette("openai_completion_error.yaml"):
            llm.generate([12345, 123456])


@pytest.mark.skipif(LANGCHAIN_VERSION < (0, 2), reason="Requires separate cassette for langchain v0.1")
@pytest.mark.snapshot
def test_cohere_llm_sync(langchain_cohere, request_vcr):
    llm = langchain_cohere.llms.Cohere(cohere_api_key=os.getenv("COHERE_API_KEY", "<not-a-real-key>"))
    with request_vcr.use_cassette("cohere_completion_sync.yaml"):
        llm.invoke("What is the secret Krabby Patty recipe?")


@pytest.mark.skipif(
    LANGCHAIN_VERSION < (0, 2) or sys.version_info < (3, 10),
    reason="Requires separate cassette for langchain v0.1, Python 3.9",
)
@pytest.mark.snapshot
def test_ai21_llm_sync(langchain_community, request_vcr):
    if langchain_community is None:
        pytest.skip("langchain-community not installed which is required for this test.")
    llm = langchain_community.llms.AI21(ai21_api_key=os.getenv("AI21_API_KEY", "<not-a-real-key>"))
    with request_vcr.use_cassette("ai21_completion_sync.yaml"):
        llm.invoke("Why does everyone in Bikini Bottom hate Plankton?")


@pytest.mark.skip(reason="This test is flaky and needs to be fixed.")
def test_openai_llm_metrics(
    langchain_community, langchain_openai, request_vcr, mock_metrics, mock_logs, snapshot_tracer
):
    llm = langchain_openai.OpenAI()
    with request_vcr.use_cassette("openai_completion_sync.yaml"):
        llm.invoke("Can you explain what Descartes meant by 'I think, therefore I am'?")
    expected_tags = [
        "version:",
        "env:",
        "service:tests.contrib.langchain",
        "langchain.request.provider:openai",
        "langchain.request.model:gpt-3.5-turbo-instruct",
        "langchain.request.type:llm",
        "langchain.request.api_key:...key>",
        "error:0",
    ]
    mock_metrics.assert_has_calls(
        [
            mock.call.distribution("tokens.prompt", 17, tags=expected_tags),
            mock.call.distribution("tokens.completion", 256, tags=expected_tags),
            mock.call.distribution("tokens.total", 273, tags=expected_tags),
            mock.call.distribution("request.duration", mock.ANY, tags=expected_tags),
        ],
        any_order=True,
    )
    if langchain_community:
        mock_metrics.increment.assert_called_once_with("tokens.total_cost", mock.ANY, tags=expected_tags)
    mock_logs.assert_not_called()


@pytest.mark.parametrize(
    "ddtrace_config_langchain",
    [dict(metrics_enabled=False, logs_enabled=True, log_prompt_completion_sample_rate=1.0)],
)
def test_llm_logs(langchain_openai, ddtrace_config_langchain, request_vcr, mock_logs, mock_metrics, mock_tracer):
    llm = langchain_openai.OpenAI()
    with request_vcr.use_cassette("openai_completion_sync.yaml"):
        llm.invoke("Can you explain what Descartes meant by 'I think, therefore I am'?")
    span = mock_tracer.pop_traces()[0][0]
    trace_id, span_id = span.trace_id, span.span_id

    assert mock_logs.enqueue.call_count == 1
    mock_logs.assert_has_calls(
        [
            mock.call.enqueue(
                {
                    "timestamp": mock.ANY,
                    "message": "sampled langchain_openai.llms.base.OpenAI",
                    "hostname": mock.ANY,
                    "ddsource": "langchain",
                    "service": "tests.contrib.langchain",
                    "status": "info",
                    "ddtags": "env:,version:,langchain.request.provider:openai,langchain.request.model:gpt-3.5-turbo-instruct,langchain.request.type:llm,langchain.request.api_key:...key>",  # noqa: E501
                    "dd.trace_id": hex(trace_id)[2:],
                    "dd.span_id": str(span_id),
                    "prompts": ["Can you explain what Descartes meant by 'I think, therefore I am'?"],
                    "choices": mock.ANY,
                }
            ),
        ]
    )
    mock_metrics.increment.assert_not_called()
    mock_metrics.distribution.assert_not_called()
    mock_metrics.count.assert_not_called()


@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_openai_chat_model_sync_call_langchain_openai(langchain_openai, request_vcr):
    chat = langchain_openai.ChatOpenAI(temperature=0, max_tokens=256)
    with request_vcr.use_cassette("openai_chat_completion_sync_call.yaml"):
        chat.invoke(input=[langchain_core.messages.HumanMessage(content="When do you use 'whom' instead of 'who'?")])


@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_openai_chat_model_sync_generate(langchain_openai, request_vcr):
    chat = langchain_openai.ChatOpenAI(temperature=0, max_tokens=256)
    with request_vcr.use_cassette("openai_chat_completion_sync_generate.yaml"):
        chat.generate(
            [
                [
                    langchain_core.messages.SystemMessage(content="Respond like a frat boy."),
                    langchain_core.messages.HumanMessage(
                        content="Where's the nearest equinox gym from Hudson Yards manhattan?"
                    ),
                ],
                [
                    langchain_core.messages.SystemMessage(content="Respond with a pirate accent."),
                    langchain_core.messages.HumanMessage(content="How does one get to Bikini Bottom from New York?"),
                ],
            ]
        )


@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_openai_chat_model_vision_generate(langchain_openai, request_vcr):
    """
    Test that input messages with nested contents are still tagged without error
    Regression test for https://github.com/DataDog/dd-trace-py/issues/8149.
    """
    image_url = (
        "https://upload.wikimedia.org/wikipedia/commons/thumb/d/dd/Gfp-wisconsin-madison-the-nature-boardwalk"
        ".jpg/2560px-Gfp-wisconsin-madison-the-nature-boardwalk.jpg"
    )
    chat = langchain_openai.ChatOpenAI(model="gpt-4-vision-preview", temperature=0, max_tokens=256)
    with request_vcr.use_cassette("openai_chat_completion_image_input_sync_generate.yaml"):
        chat.generate(
            [
                [
                    langchain_core.messages.HumanMessage(
                        content=[
                            {"type": "text", "text": "What’s in this image?"},
                            {
                                "type": "image_url",
                                "image_url": image_url,
                            },
                        ],
                    ),
                ],
            ]
        )


@pytest.mark.asyncio
@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
async def test_openai_chat_model_async_call(langchain_openai, request_vcr):
    chat = langchain_openai.ChatOpenAI(temperature=0, max_tokens=256)
    with request_vcr.use_cassette("openai_chat_completion_async_call.yaml"):
        await chat._call_async(
            [langchain_core.messages.HumanMessage(content="When do you use 'whom' instead of 'who'?")]
        )


@flaky(until=1735812000, reason="Batch call has a non-deterministic response order.")
@pytest.mark.asyncio
@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
async def test_openai_chat_model_async_generate(langchain_openai, request_vcr):
    chat = langchain_openai.ChatOpenAI(temperature=0, max_tokens=256)
    with request_vcr.use_cassette("openai_chat_completion_async_generate.yaml"):
        await chat.agenerate(
            [
                [
                    langchain_core.messages.SystemMessage(content="Respond like a frat boy."),
                    langchain_core.messages.HumanMessage(
                        content="Where's the nearest equinox gym from Hudson Yards manhattan?"
                    ),
                ],
                [
                    langchain_core.messages.SystemMessage(content="Respond with a pirate accent."),
                    langchain_core.messages.HumanMessage(content="How does one get to Bikini Bottom from New York?"),
                ],
            ]
        )


@pytest.mark.skip(reason="This test is flaky and needs to be fixed.")
def test_chat_model_metrics(
    langchain_core, langchain_community, langchain_openai, request_vcr, mock_metrics, mock_logs, snapshot_tracer
):
    chat = langchain_openai.ChatOpenAI(temperature=0, max_tokens=256)
    with request_vcr.use_cassette("openai_chat_completion_sync_call.yaml"):
        chat.invoke(input=[langchain_core.messages.HumanMessage(content="When do you use 'whom' instead of 'who'?")])
    expected_tags = [
        "version:",
        "env:",
        "service:tests.contrib.langchain",
        "langchain.request.provider:openai",
        "langchain.request.model:gpt-3.5-turbo",
        "langchain.request.type:chat_model",
        "langchain.request.api_key:...key>",
        "error:0",
    ]
    mock_metrics.assert_has_calls(
        [
            mock.call.distribution("tokens.prompt", 20, tags=expected_tags),
            mock.call.distribution("tokens.completion", 83, tags=expected_tags),
            mock.call.distribution("tokens.total", 103, tags=expected_tags),
            mock.call.distribution("request.duration", mock.ANY, tags=expected_tags),
        ],
        any_order=True,
    )
    if langchain_community:
        mock_metrics.increment.assert_called_once_with("tokens.total_cost", mock.ANY, tags=expected_tags)
    mock_logs.assert_not_called()


@pytest.mark.parametrize(
    "ddtrace_config_langchain",
    [dict(metrics_enabled=False, logs_enabled=True, log_prompt_completion_sample_rate=1.0)],
)
def test_chat_model_logs(
    langchain_core, langchain_openai, ddtrace_config_langchain, request_vcr, mock_logs, mock_metrics, mock_tracer
):
    chat = langchain_openai.ChatOpenAI(temperature=0, max_tokens=256)
    with request_vcr.use_cassette("openai_chat_completion_sync_call.yaml"):
        chat.invoke(input=[langchain_core.messages.HumanMessage(content="When do you use 'whom' instead of 'who'?")])
    span = mock_tracer.pop_traces()[0][0]
    trace_id, span_id = span.trace_id, span.span_id

    assert mock_logs.enqueue.call_count == 1
    mock_logs.assert_has_calls(
        [
            mock.call.enqueue(
                {
                    "timestamp": mock.ANY,
                    "message": "sampled langchain_openai.chat_models.base.ChatOpenAI",
                    "hostname": mock.ANY,
                    "ddsource": "langchain",
                    "service": "tests.contrib.langchain",
                    "status": "info",
                    "ddtags": "env:,version:,langchain.request.provider:openai,langchain.request.model:gpt-3.5-turbo,langchain.request.type:chat_model,langchain.request.api_key:...key>",  # noqa: E501
                    "dd.trace_id": hex(trace_id)[2:],
                    "dd.span_id": str(span_id),
                    "messages": [
                        [
                            {
                                "content": "When do you use 'whom' instead of 'who'?",
                                "message_type": "HumanMessage",
                            }
                        ]
                    ],
                    "choices": [
                        [
                            {
                                "content": mock.ANY,
                                "message_type": "AIMessage",
                            }
                        ]
                    ],
                }
            ),
        ]
    )
    mock_metrics.increment.assert_not_called()
    mock_metrics.distribution.assert_not_called()
    mock_metrics.count.assert_not_called()


@pytest.mark.snapshot
def test_openai_embedding_query(langchain_openai, request_vcr):
    with mock.patch("langchain_openai.OpenAIEmbeddings._get_len_safe_embeddings", return_value=[0.0] * 1536):
        embeddings = langchain_openai.OpenAIEmbeddings()
        with request_vcr.use_cassette("openai_embedding_query.yaml"):
            embeddings.embed_query("this is a test query.")


@pytest.mark.snapshot
def test_fake_embedding_query(langchain_community):
    if langchain_community is None:
        pytest.skip("langchain-community not installed which is required for this test.")
    embeddings = langchain_community.embeddings.FakeEmbeddings(size=99)
    embeddings.embed_query(text="foo")


@pytest.mark.snapshot
def test_fake_embedding_document(langchain_community):
    if langchain_community is None:
        pytest.skip("langchain-community not installed which is required for this test.")
    embeddings = langchain_community.embeddings.FakeEmbeddings(size=99)
    embeddings.embed_documents(texts=["foo", "bar"])


@pytest.mark.skip(reason="This test is flaky and needs to be fixed.")
def test_openai_embedding_metrics(langchain_openai, request_vcr, mock_metrics, mock_logs, snapshot_tracer):
    with mock.patch("langchain_openai.OpenAIEmbeddings._get_len_safe_embeddings", return_value=[0.0] * 1536):
        embeddings = langchain_openai.OpenAIEmbeddings()
        with request_vcr.use_cassette("openai_embedding_query.yaml"):
            embeddings.embed_query("this is a test query.")
    expected_tags = [
        "version:",
        "env:",
        "service:tests.contrib.langchain",
        "langchain.request.provider:openai",
        "langchain.request.model:text-embedding-ada-002",
        "langchain.request.type:embedding",
        "langchain.request.api_key:...key>",
        "error:0",
    ]
    mock_metrics.assert_has_calls(
        [mock.call.distribution("request.duration", mock.ANY, tags=expected_tags)],
        any_order=True,
    )
    mock_logs.assert_not_called()


@pytest.mark.parametrize(
    "ddtrace_config_langchain",
    [dict(metrics_enabled=False, logs_enabled=True, log_prompt_completion_sample_rate=1.0)],
)
@pytest.mark.skip(reason="This test is flaky and needs to be fixed.")
def test_embedding_logs(langchain_openai, ddtrace_config_langchain, request_vcr, mock_logs, mock_metrics, mock_tracer):
    with mock.patch("langchain_openai.OpenAIEmbeddings._get_len_safe_embeddings", return_value=[0.0] * 1536):
        embeddings = langchain_openai.OpenAIEmbeddings()
        with request_vcr.use_cassette("openai_embedding_query.yaml"):
            embeddings.embed_query("this is a test query.")
    span = mock_tracer.pop_traces()[0][0]
    trace_id, span_id = span.trace_id, span.span_id

    assert mock_logs.enqueue.call_count == 1
    mock_logs.assert_has_calls(
        [
            mock.call.enqueue(
                {
                    "timestamp": mock.ANY,
                    "message": "sampled langchain_openai.embeddings.base.OpenAIEmbeddings",
                    "hostname": mock.ANY,
                    "ddsource": "langchain",
                    "service": "tests.contrib.langchain",
                    "status": "info",
                    "ddtags": "env:,version:,langchain.request.provider:openai,langchain.request.model:text-embedding-ada-002,langchain.request.type:embedding,langchain.request.api_key:...key>",  # noqa: E501
                    "dd.trace_id": hex(trace_id)[2:],
                    "dd.span_id": str(span_id),
                    "inputs": ["this is a test query."],
                }
            ),
        ]
    )
    mock_metrics.increment.assert_not_called()
    mock_metrics.distribution.assert_not_called()
    mock_metrics.count.assert_not_called()


@pytest.mark.snapshot
def test_pinecone_vectorstore_similarity_search(langchain_openai, request_vcr):
    """
    Test that calling a similarity search on a Pinecone vectorstore with langchain will
    result in a 2-span trace with a vectorstore span and underlying OpenAI embedding interface span.
    """
    import langchain_pinecone
    import pinecone

    with mock.patch("langchain_openai.OpenAIEmbeddings._get_len_safe_embeddings", return_value=[0.0] * 1536):
        with request_vcr.use_cassette("openai_pinecone_similarity_search.yaml"):
            pc = pinecone.Pinecone(
                api_key=os.getenv("PINECONE_API_KEY", "<not-a-real-key>"),
                environment=os.getenv("PINECONE_ENV", "<not-a-real-env>"),
            )
            embed = langchain_openai.OpenAIEmbeddings(model="text-embedding-ada-002")
            index = pc.Index("langchain-retrieval")
            vectorstore = langchain_pinecone.PineconeVectorStore(index, embed, "text")
            vectorstore.similarity_search("Who was Alan Turing?", 1)


@pytest.mark.snapshot(
    ignores=IGNORE_FIELDS
    + ["meta.langchain.response.outputs.input_documents", "meta.langchain.request.inputs.input_documents"]
)
@pytest.mark.skip(reason="This test is flaky and needs to be fixed.")
def test_pinecone_vectorstore_retrieval_chain(langchain_openai, request_vcr):
    """
    Test that calling a similarity search on a Pinecone vectorstore with langchain will
    result in a 2-span trace with a vectorstore span and underlying OpenAI embedding interface span.
    """
    import langchain_pinecone
    import pinecone

    with mock.patch("langchain_openai.OpenAIEmbeddings._get_len_safe_embeddings", return_value=[[0.0] * 1536]):
        with request_vcr.use_cassette("openai_pinecone_vectorstore_retrieval_chain.yaml"):
            pc = pinecone.Pinecone(
                api_key=os.getenv("PINECONE_API_KEY", "<not-a-real-key>"),
                environment=os.getenv("PINECONE_ENV", "<not-a-real-env>"),
            )
            embed = langchain_openai.OpenAIEmbeddings(model="text-embedding-ada-002")
            index = pc.Index("langchain-retrieval")
            vectorstore = langchain_pinecone.PineconeVectorStore(index, embed, "text")

            llm = langchain_openai.OpenAI()
            qa_with_sources = langchain.chains.RetrievalQAWithSourcesChain.from_chain_type(
                llm=llm, chain_type="stuff", retriever=vectorstore.as_retriever()
            )
            qa_with_sources.invoke("What did the president say about Ketanji Brown Jackson?")


@pytest.mark.skip(reason="This test is flaky and needs to be fixed.")
def test_vectorstore_similarity_search_metrics(langchain_openai, request_vcr, mock_metrics, mock_logs, snapshot_tracer):
    import langchain_pinecone
    import pinecone

    with mock.patch("langchain_openai.OpenAIEmbeddings._get_len_safe_embeddings", return_value=[0.0] * 1536):
        with request_vcr.use_cassette("openai_pinecone_similarity_search.yaml"):
            pc = pinecone.Pinecone(
                api_key=os.getenv("PINECONE_API_KEY", "<not-a-real-key>"),
                environment=os.getenv("PINECONE_ENV", "<not-a-real-env>"),
            )
            embed = langchain_openai.OpenAIEmbeddings(model="text-embedding-ada-002")
            index = pc.Index("langchain-retrieval")
            vectorstore = langchain_pinecone.PineconeVectorStore(index, embed, "text")
            vectorstore.similarity_search("Who was Alan Turing?", 1)
    expected_tags = [
        "version:",
        "env:",
        "service:tests.contrib.langchain",
        "langchain.request.provider:pineconevectorstore",
        "langchain.request.model:",
        "langchain.request.type:similarity_search",
        "langchain.request.api_key:",
        "error:0",
    ]
    mock_metrics.distribution.assert_called_with("request.duration", mock.ANY, tags=expected_tags),
    mock_logs.assert_not_called()


@pytest.mark.parametrize(
    "ddtrace_config_langchain",
    [dict(metrics_enabled=False, logs_enabled=True, log_prompt_completion_sample_rate=1.0)],
)
@pytest.mark.skip(reason="This test is flaky and needs to be fixed.")
def test_vectorstore_logs(
    langchain_openai, ddtrace_config_langchain, request_vcr, mock_logs, mock_metrics, mock_tracer
):
    import langchain_pinecone
    import pinecone

    with mock.patch("langchain_openai.OpenAIEmbeddings._get_len_safe_embeddings", return_value=[0.0] * 1536):
        with request_vcr.use_cassette("openai_pinecone_similarity_search.yaml"):
            pc = pinecone.Pinecone(
                api_key=os.getenv("PINECONE_API_KEY", "<not-a-real-key>"),
                environment=os.getenv("PINECONE_ENV", "<not-a-real-env>"),
            )
            embed = langchain_openai.OpenAIEmbeddings(model="text-embedding-ada-002")
            index = pc.Index("langchain-retrieval")
            vectorstore = langchain_pinecone.PineconeVectorStore(index, embed, "text")
            vectorstore.similarity_search("Who was Alan Turing?", 1)
    traces = mock_tracer.pop_traces()
    vectorstore_span = traces[0][0]
    embeddings_span = traces[0][1]

    assert mock_logs.enqueue.call_count == 2  # This operation includes 1 vectorstore call and 1 embeddings call
    mock_logs.assert_has_calls(
        [
            mock.call.enqueue(
                {
                    "timestamp": mock.ANY,
                    "message": "sampled langchain_openai.embeddings.base.OpenAIEmbeddings",
                    "hostname": mock.ANY,
                    "ddsource": "langchain",
                    "service": "tests.contrib.langchain",
                    "status": "info",
                    "ddtags": "env:,version:,langchain.request.provider:openai,langchain.request.model:text-embedding-ada-002,langchain.request.type:embedding,langchain.request.api_key:...key>",  # noqa: E501
                    "dd.trace_id": hex(embeddings_span.trace_id)[2:],
                    "dd.span_id": str(embeddings_span.span_id),
                    "inputs": ["Who was Alan Turing?"],
                }
            ),
            mock.call.enqueue(
                {
                    "timestamp": mock.ANY,
                    "message": "sampled langchain_pinecone.vectorstores.PineconeVectorStore",
                    "hostname": mock.ANY,
                    "ddsource": "langchain",
                    "service": "tests.contrib.langchain",
                    "status": "info",
                    "ddtags": "env:,version:,langchain.request.provider:pineconevectorstore,langchain.request.model:,langchain.request.type:similarity_search,langchain.request.api_key:",  # noqa: E501
                    "dd.trace_id": hex(vectorstore_span.trace_id)[2:],
                    "dd.span_id": str(vectorstore_span.span_id),
                    "query": "Who was Alan Turing?",
                    "k": 1,
                    "documents": mock.ANY,
                }
            ),
        ]
    )
    mock_metrics.increment.assert_not_called()
    mock_metrics.distribution.assert_not_called()
    mock_metrics.count.assert_not_called()


@pytest.mark.parametrize(
    "ddtrace_config_langchain",
    [dict(metrics_enabled=False, logs_enabled=True, log_prompt_completion_sample_rate=1.0)],
)
def test_llm_logs_when_response_not_completed(
    langchain_openai, ddtrace_config_langchain, mock_logs, mock_metrics, mock_tracer
):
    """Test that errors get logged even if the response is not returned."""
    with mock.patch("langchain_openai.OpenAI._generate", side_effect=Exception("Mocked Error")):
        with pytest.raises(Exception) as exc_info:
            llm = langchain_openai.OpenAI()
            llm.invoke("Can you please not return an error?")
        assert str(exc_info.value) == "Mocked Error"
    span = mock_tracer.pop_traces()[0][0]
    trace_id, span_id = span.trace_id, span.span_id

    assert mock_logs.enqueue.call_count == 1
    mock_logs.assert_has_calls(
        [
            mock.call.enqueue(
                {
                    "timestamp": mock.ANY,
                    "message": "sampled langchain_openai.llms.base.OpenAI",
                    "hostname": mock.ANY,
                    "ddsource": "langchain",
                    "service": "tests.contrib.langchain",
                    "status": "error",
                    "ddtags": "env:,version:,langchain.request.provider:openai,langchain.request.model:gpt-3.5-turbo-instruct,langchain.request.type:llm,langchain.request.api_key:...key>",  # noqa: E501
                    "dd.trace_id": hex(trace_id)[2:],
                    "dd.span_id": str(span_id),
                    "prompts": ["Can you please not return an error?"],
                    "choices": [],
                }
            ),
        ]
    )


@pytest.mark.parametrize(
    "ddtrace_config_langchain",
    [dict(metrics_enabled=False, logs_enabled=True, log_prompt_completion_sample_rate=1.0)],
)
def test_chat_model_logs_when_response_not_completed(
    langchain_core, langchain_openai, ddtrace_config_langchain, mock_logs, mock_metrics, mock_tracer
):
    """Test that errors get logged even if the response is not returned."""
    with mock.patch("langchain_openai.ChatOpenAI._generate", side_effect=Exception("Mocked Error")):
        with pytest.raises(Exception) as exc_info:
            chat = langchain_openai.ChatOpenAI(temperature=0, max_tokens=256)
            chat.invoke(input=[langchain_core.messages.HumanMessage(content="Can you please not return an error?")])
        assert str(exc_info.value) == "Mocked Error"
    span = mock_tracer.pop_traces()[0][0]
    trace_id, span_id = span.trace_id, span.span_id

    assert mock_logs.enqueue.call_count == 1
    mock_logs.enqueue.assert_called_with(
        {
            "timestamp": mock.ANY,
            "message": "sampled langchain_openai.chat_models.base.ChatOpenAI",
            "hostname": mock.ANY,
            "ddsource": "langchain",
            "service": "tests.contrib.langchain",
            "status": "error",
            "ddtags": "env:,version:,langchain.request.provider:openai,langchain.request.model:gpt-3.5-turbo,langchain.request.type:chat_model,langchain.request.api_key:...key>",  # noqa: E501
            "dd.trace_id": hex(trace_id)[2:],
            "dd.span_id": str(span_id),
            "messages": [
                [
                    {
                        "content": "Can you please not return an error?",
                        "message_type": "HumanMessage",
                    }
                ]
            ],
            "choices": [],
        }
    )


@pytest.mark.parametrize(
    "ddtrace_config_langchain",
    [dict(metrics_enabled=False, logs_enabled=True, log_prompt_completion_sample_rate=1.0)],
)
@pytest.mark.skip(reason="This test is flaky and needs to be fixed.")
def test_embedding_logs_when_response_not_completed(
    langchain_openai, ddtrace_config_langchain, mock_logs, mock_metrics, mock_tracer
):
    """Test that errors get logged even if the response is not returned."""
    with mock.patch(
        "langchain_openai.OpenAIEmbeddings._get_len_safe_embeddings", side_effect=Exception("Mocked Error")
    ):
        with pytest.raises(Exception) as exc_info:
            embeddings = langchain_openai.OpenAIEmbeddings()
            embeddings.embed_query("Can you please not return an error?")
        assert str(exc_info.value) == "Mocked Error"
    span = mock_tracer.pop_traces()[0][0]
    trace_id, span_id = span.trace_id, span.span_id

    assert mock_logs.enqueue.call_count == 1
    mock_logs.assert_has_calls(
        [
            mock.call.enqueue(
                {
                    "timestamp": mock.ANY,
                    "message": "sampled langchain_openai.embeddings.base.OpenAIEmbeddings",
                    "hostname": mock.ANY,
                    "ddsource": "langchain",
                    "service": "tests.contrib.langchain",
                    "status": "error",
                    "ddtags": "env:,version:,langchain.request.provider:openai,langchain.request.model:text-embedding-ada-002,langchain.request.type:embedding,langchain.request.api_key:...key>",  # noqa: E501
                    "dd.trace_id": hex(trace_id)[2:],
                    "dd.span_id": str(span_id),
                    "inputs": ["Can you please not return an error?"],
                }
            ),
        ]
    )


@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_lcel_chain_simple(langchain_core, langchain_openai, request_vcr):
    prompt = langchain_core.prompts.ChatPromptTemplate.from_messages(
        [("system", "You are world class technical documentation writer."), ("user", "{input}")]
    )
    llm = langchain_openai.OpenAI()

    chain = prompt | llm
    with request_vcr.use_cassette("lcel_openai_chain_call.yaml"):
        chain.invoke({"input": "how can langsmith help with testing?"})


@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_lcel_chain_complicated(langchain_core, langchain_openai, request_vcr):
    prompt = langchain_core.prompts.ChatPromptTemplate.from_template(
        "Tell me a short joke about {topic} in the style of {style}"
    )

    chat_openai = langchain_openai.ChatOpenAI()
    openai = langchain_openai.OpenAI()

    model = chat_openai.configurable_alternatives(
        langchain_core.runnables.ConfigurableField(id="model"),
        default_key="chat_openai",
        openai=openai,
    )

    chain = (
        {
            "topic": langchain_core.runnables.RunnablePassthrough(),
            "style": langchain_core.runnables.RunnablePassthrough(),
        }
        | prompt
        | model
        | langchain_core.output_parsers.StrOutputParser()
    )

    with request_vcr.use_cassette("lcel_openai_chain_call_complicated.yaml"):
        chain.invoke({"topic": "chickens", "style": "a 90s rapper"})


@pytest.mark.asyncio
@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
async def test_lcel_chain_simple_async(langchain_core, langchain_openai, request_vcr):
    prompt = langchain_core.prompts.ChatPromptTemplate.from_messages(
        [("system", "You are world class technical documentation writer."), ("user", "{input}")]
    )
    llm = langchain_openai.OpenAI()

    chain = prompt | llm
    with request_vcr.use_cassette("lcel_openai_chain_acall.yaml"):
        await chain.ainvoke({"input": "how can langsmith help with testing?"})


@flaky(1735812000, reason="batch() is non-deterministic in which order it processes inputs")
@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
@pytest.mark.skipif(sys.version_info >= (3, 11), reason="Python <3.11 test")
def test_lcel_chain_batch(langchain_core, langchain_openai, request_vcr):
    """
    Test that invoking a chain with a batch of inputs will result in a 4-span trace,
    with a root RunnableSequence span, then 3 LangChain ChatOpenAI spans underneath
    """
    prompt = langchain_core.prompts.ChatPromptTemplate.from_template("Tell me a short joke about {topic}")
    output_parser = langchain_core.output_parsers.StrOutputParser()
    model = langchain_openai.ChatOpenAI()
    chain = {"topic": langchain_core.runnables.RunnablePassthrough()} | prompt | model | output_parser

    with request_vcr.use_cassette("lcel_openai_chain_batch.yaml"):
        chain.batch(inputs=["chickens", "pigs"])


@flaky(1735812000, reason="batch() is non-deterministic in which order it processes inputs")
@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
@pytest.mark.skipif(sys.version_info < (3, 11), reason="Python 3.11+ required")
def test_lcel_chain_batch_311(langchain_core, langchain_openai, request_vcr):
    """
    Test that invoking a chain with a batch of inputs will result in a 4-span trace,
    with a root RunnableSequence span, then 3 LangChain ChatOpenAI spans underneath
    """
    prompt = langchain_core.prompts.ChatPromptTemplate.from_template("Tell me a short joke about {topic}")
    output_parser = langchain_core.output_parsers.StrOutputParser()
    model = langchain_openai.ChatOpenAI()
    chain = {"topic": langchain_core.runnables.RunnablePassthrough()} | prompt | model | output_parser

    with request_vcr.use_cassette("lcel_openai_chain_batch_311.yaml"):
        chain.batch(inputs=["chickens", "pigs"])


@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_lcel_chain_nested(langchain_core, langchain_openai, request_vcr):
    """
    Test that invoking a nested chain will result in a 4-span trace with a root
    RunnableSequence span (complete_chain), then another RunnableSequence (chain1) +
    LangChain ChatOpenAI span (chain1's llm call) and finally a second LangChain ChatOpenAI span (chain2's llm call)
    """
    prompt1 = langchain_core.prompts.ChatPromptTemplate.from_template("what is the city {person} is from?")
    prompt2 = langchain_core.prompts.ChatPromptTemplate.from_template(
        "what country is the city {city} in? respond in {language}"
    )

    model = langchain_openai.ChatOpenAI()

    chain1 = prompt1 | model | langchain_core.output_parsers.StrOutputParser()
    chain2 = prompt2 | model | langchain_core.output_parsers.StrOutputParser()

    complete_chain = {"city": chain1, "language": itemgetter("language")} | chain2

    with request_vcr.use_cassette("lcel_openai_chain_nested.yaml"):
        complete_chain.invoke({"person": "Spongebob Squarepants", "language": "Spanish"})


@flaky(1735812000, reason="batch() is non-deterministic in which order it processes inputs")
@pytest.mark.asyncio
@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
async def test_lcel_chain_batch_async(langchain_core, langchain_openai, request_vcr):
    """
    Test that invoking a chain with a batch of inputs will result in a 4-span trace,
    with a root RunnableSequence span, then 3 LangChain ChatOpenAI spans underneath
    """
    prompt = langchain_core.prompts.ChatPromptTemplate.from_template("Tell me a short joke about {topic}")
    output_parser = langchain_core.output_parsers.StrOutputParser()
    model = langchain_openai.ChatOpenAI()
    chain = {"topic": langchain_core.runnables.RunnablePassthrough()} | prompt | model | output_parser

    with request_vcr.use_cassette("lcel_openai_chain_batch_async.yaml"):
        await chain.abatch(inputs=["chickens", "pigs"])


@pytest.mark.snapshot
def test_lcel_chain_non_dict_input(langchain_core):
    """
    Tests that non-dict inputs (specifically also non-string) are stringified properly
    """
    add_one = langchain_core.runnables.RunnableLambda(lambda x: x + 1)
    multiply_two = langchain_core.runnables.RunnableLambda(lambda x: x * 2)
    sequence = add_one | multiply_two

    sequence.invoke(1)


@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_lcel_with_tools_openai(langchain_core, langchain_openai, request_vcr):
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
    with request_vcr.use_cassette("lcel_with_tools_openai.yaml"):
        llm_with_tools.invoke("What is the sum of 1 and 2?")


@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_lcel_with_tools_anthropic(langchain_core, langchain_anthropic, request_vcr):
    import langchain_core.tools

    @langchain_core.tools.tool
    def add(a: int, b: int) -> int:
        """Adds a and b.

        Args:
            a: first int
            b: second int
        """
        return a + b

    llm = langchain_anthropic.ChatAnthropic(temperature=1, model_name="claude-3-opus-20240229")
    llm_with_tools = llm.bind_tools([add])
    with request_vcr.use_cassette("lcel_with_tools_anthropic.yaml"):
        llm_with_tools.invoke("What is the sum of 1 and 2?")


@pytest.mark.snapshot
def test_faiss_vectorstore_retrieval(langchain_community, langchain_openai, request_vcr):
    if langchain_community is None:
        pytest.skip("langchain-community not installed which is required for this test.")
    pytest.importorskip("faiss", reason="faiss required for this test.")
    with mock.patch("langchain_openai.OpenAIEmbeddings._get_len_safe_embeddings", return_value=[[0.0] * 1536]):
        with request_vcr.use_cassette("openai_embedding_query.yaml"):
            faiss = langchain_community.vectorstores.faiss.FAISS.from_texts(
                ["this is a test query."],
                embedding=langchain_openai.OpenAIEmbeddings(),
            )
            retriever = faiss.as_retriever()
        with request_vcr.use_cassette("openai_retrieval_embedding.yaml"):
            retriever.invoke("What was the message of the last test query?")


@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_streamed_chain(langchain_core, langchain_openai, streamed_response_responder):
    client = streamed_response_responder(
        module="openai",
        client_class_key="OpenAI",
        http_client_key="http_client",
        endpoint_path=["chat", "completions"],
        file="lcel_openai_chat_streamed_response.txt",
    )

    prompt = langchain_core.prompts.ChatPromptTemplate.from_messages(
        [("system", "You are world class technical documentation writer."), ("user", "{input}")]
    )
    llm = langchain_openai.ChatOpenAI(client=client)
    parser = langchain_core.output_parsers.StrOutputParser()

    chain = prompt | llm | parser
    for _ in chain.stream({"input": "how can langsmith help with testing?"}):
        pass


@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_streamed_chat(langchain_openai, streamed_response_responder):
    client = streamed_response_responder(
        module="openai",
        client_class_key="OpenAI",
        http_client_key="http_client",
        endpoint_path=["chat", "completions"],
        file="lcel_openai_chat_streamed_response.txt",
    )
    model = langchain_openai.ChatOpenAI(client=client)

    for _ in model.stream(input="how can langsmith help with testing?"):
        pass


@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_streamed_llm(langchain_openai, streamed_response_responder):
    client = streamed_response_responder(
        module="openai",
        client_class_key="OpenAI",
        http_client_key="http_client",
        endpoint_path=["completions"],
        file="lcel_openai_llm_streamed_response.txt",
    )

    llm = langchain_openai.OpenAI(client=client)

    for _ in llm.stream(input="How do I write technical documentation?"):
        pass


@pytest.mark.snapshot(
    ignores=IGNORE_FIELDS,
    token="tests.contrib.langchain.test_langchain.test_streamed_chain",
)
async def test_astreamed_chain(langchain_core, langchain_openai, async_streamed_response_responder):
    client = async_streamed_response_responder(
        module="openai",
        client_class_key="AsyncOpenAI",
        http_client_key="http_client",
        endpoint_path=["chat", "completions"],
        file="lcel_openai_chat_streamed_response.txt",
    )

    prompt = langchain_core.prompts.ChatPromptTemplate.from_messages(
        [("system", "You are world class technical documentation writer."), ("user", "{input}")]
    )
    llm = langchain_openai.ChatOpenAI(async_client=client)
    parser = langchain_core.output_parsers.StrOutputParser()

    chain = prompt | llm | parser
    async for _ in chain.astream({"input": "how can langsmith help with testing?"}):
        pass


@pytest.mark.snapshot(
    ignores=IGNORE_FIELDS,
    token="tests.contrib.langchain.test_langchain.test_streamed_chat",
)
async def test_astreamed_chat(langchain_openai, async_streamed_response_responder):
    client = async_streamed_response_responder(
        module="openai",
        client_class_key="AsyncOpenAI",
        http_client_key="http_client",
        endpoint_path=["chat", "completions"],
        file="lcel_openai_chat_streamed_response.txt",
    )

    model = langchain_openai.ChatOpenAI(async_client=client)

    async for _ in model.astream(input="how can langsmith help with testing?"):
        pass


@pytest.mark.snapshot(
    ignores=IGNORE_FIELDS,
    token="tests.contrib.langchain.test_langchain.test_streamed_llm",
)
async def test_astreamed_llm(langchain_openai, async_streamed_response_responder):
    client = async_streamed_response_responder(
        module="openai",
        client_class_key="AsyncOpenAI",
        http_client_key="http_client",
        endpoint_path=["completions"],
        file="lcel_openai_llm_streamed_response.txt",
    )

    llm = langchain_openai.OpenAI(async_client=client)

    async for _ in llm.astream(input="How do I write technical documentation?"):
        pass


@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_streamed_json_output_parser(langchain_core, langchain_openai, streamed_response_responder):
    client = streamed_response_responder(
        module="openai",
        client_class_key="OpenAI",
        http_client_key="http_client",
        endpoint_path=["chat", "completions"],
        file="lcel_openai_chat_streamed_response_json_output_parser.txt",
    )

    model = langchain_openai.ChatOpenAI(model="gpt-4o", max_tokens=50, client=client)
    parser = langchain_core.output_parsers.JsonOutputParser()

    chain = model | parser
    inp = (
        "output a list of the country france their population in JSON format. "
        'Use a dict with an outer key of "countries" which contains a list of countries. '
        "Each country should have the key `name` and `population`"
    )

    messages = [
        langchain_core.messages.SystemMessage(content="You know everything about the world."),
        langchain_core.messages.HumanMessage(content=inp),
    ]

    for _ in chain.stream(input=messages):
        pass


# until we fully support `astream_events`, we do not need a snapshot here
# this is just a regression test to make sure we don't throw
async def test_astreamed_events_does_not_throw(langchain_openai, langchain_core, async_streamed_response_responder):
    client = async_streamed_response_responder(
        module="openai",
        client_class_key="AsyncOpenAI",
        http_client_key="http_client",
        endpoint_path=["chat", "completions"],
        file="lcel_openai_chat_streamed_response.txt",
    )

    model = langchain_openai.ChatOpenAI(async_client=client)
    parser = langchain_core.output_parsers.StrOutputParser()

    chain = model | parser

    async for _ in chain.astream_events(input="some input", version="v1"):
        pass


@pytest.mark.snapshot(
    # tool description is generated differently is some langchain_core versions
    ignores=["meta.langchain.request.tool.description"],
    token="tests.contrib.langchain.test_langchain.test_base_tool_invoke",
)
def test_base_tool_invoke(langchain_core, request_vcr):
    """
    Test that invoking a tool with langchain will
    result in a 1-span trace with a tool span.
    """
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

    calculator.invoke("2")


@pytest.mark.asyncio
@pytest.mark.snapshot(
    # tool description is generated differently is some langchain_core versions
    ignores=["meta.langchain.request.tool.description"],
    token="tests.contrib.langchain.test_langchain.test_base_tool_invoke",
)
async def test_base_tool_ainvoke(langchain_core, request_vcr):
    """
    Test that invoking a tool with langchain will
    result in a 1-span trace with a tool span. Async mode
    """

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

    await calculator.ainvoke("2")


@pytest.mark.asyncio
@pytest.mark.snapshot(
    # tool description is generated differently is some langchain_core versions
    ignores=["meta.langchain.request.tool.description", "meta.langchain.request.config"],
)
def test_base_tool_invoke_non_json_serializable_config(langchain_core, request_vcr):
    """
    Test that invoking a tool with langchain will
    result in a 1-span trace with a tool span. Async mode
    """

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

    calculator.invoke("2", config={"unserializable": object()})
