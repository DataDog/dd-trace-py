import os
import re
import sys

import langchain as _langchain
import mock
import pytest

from ddtrace.internal.utils.version import parse_version
from tests.contrib.langchain.utils import get_request_vcr
from tests.contrib.langchain.utils import long_input_text
from tests.utils import override_global_config


pytestmark = pytest.mark.skipif(
    parse_version(_langchain.__version__) >= (0, 1), reason="This module only tests langchain < 0.1"
)

PY39 = sys.version_info < (3, 10)


@pytest.fixture(scope="session")
def request_vcr():
    yield get_request_vcr(subdirectory_name="langchain")


@pytest.mark.parametrize("ddtrace_config_langchain", [dict(logs_enabled=True, log_prompt_completion_sample_rate=1.0)])
def test_global_tags(ddtrace_config_langchain, langchain, request_vcr, mock_metrics, mock_logs, mock_tracer):
    """
    When the global config UST tags are set
        The service name should be used for all data
        The env should be used for all data
        The version should be used for all data
    """
    llm = langchain.llms.OpenAI(model="text-davinci-003")
    with override_global_config(dict(service="test-svc", env="staging", version="1234")):
        cassette_name = "openai_completion_sync_39.yaml" if PY39 else "openai_completion_sync.yaml"
        with request_vcr.use_cassette(cassette_name):
            llm("What does Nietzsche mean by 'God is dead'?")

    span = mock_tracer.pop_traces()[0][0]
    assert span.resource == "langchain.llms.openai.OpenAI"  # check this needs changed
    assert span.service == "test-svc"
    assert span.get_tag("env") == "staging"
    assert span.get_tag("version") == "1234"
    assert span.get_tag("langchain.request.provider") == "openai"
    assert span.get_tag("langchain.request.model") == "text-davinci-003"
    assert span.get_tag("langchain.request.api_key") == "...key>"

    assert mock_logs.enqueue.call_count == 1
    assert mock_metrics.mock_calls
    for _, _args, kwargs in mock_metrics.mock_calls:
        expected_metrics = [
            "service:test-svc",
            "env:staging",
            "version:1234",
            "langchain.request.model:text-davinci-003",
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
            == "env:staging,version:1234,langchain.request.provider:openai,langchain.request.model:text-davinci-003,langchain.request.type:llm,langchain.request.api_key:...key>"  # noqa: E501
        )


@pytest.mark.skipif(PY39, reason="Python 3.10+ specific test")
@pytest.mark.snapshot(ignores=["metrics.langchain.tokens.total_cost", "resource"])
def test_openai_llm_sync(langchain, request_vcr):
    llm = langchain.llms.OpenAI(model="text-davinci-003")
    with request_vcr.use_cassette("openai_completion_sync.yaml"):
        llm("Can you explain what Descartes meant by 'I think, therefore I am'?")


@pytest.mark.skipif(not PY39, reason="Python 3.9 specific test")
@pytest.mark.snapshot(ignores=["metrics.langchain.tokens.total_cost"])
def test_openai_llm_sync_39(langchain, request_vcr):
    llm = langchain.llms.OpenAI(model="text-davinci-003")
    with request_vcr.use_cassette("openai_completion_sync_39.yaml"):
        llm("Can you explain what Descartes meant by 'I think, therefore I am'?")


@pytest.mark.skipif(PY39, reason="Python 3.10+ specific test")
@pytest.mark.snapshot(ignores=["resource"])
def test_openai_llm_sync_multiple_prompts(langchain, request_vcr):
    llm = langchain.llms.OpenAI(model="text-davinci-003")
    with request_vcr.use_cassette("openai_completion_sync_multi_prompt.yaml"):
        llm.generate(
            prompts=[
                "What is the best way to teach a baby multiple languages?",
                "How many times has Spongebob failed his road test?",
            ]
        )


@pytest.mark.skipif(not PY39, reason="Python 3.9 specific test")
@pytest.mark.snapshot
def test_openai_llm_sync_multiple_prompts_39(langchain, request_vcr):
    llm = langchain.llms.OpenAI(model="text-davinci-003")
    with request_vcr.use_cassette("openai_completion_sync_multi_prompt_39.yaml"):
        llm.generate(
            [
                "What is the best way to teach a baby multiple languages?",
                "How many times has Spongebob failed his road test?",
            ]
        )


@pytest.mark.asyncio
@pytest.mark.snapshot(ignores=["resource", "langchain.request.openai.parameters.request_timeout"])
async def test_openai_llm_async(langchain, request_vcr):
    llm = langchain.llms.OpenAI(model="text-davinci-003")
    cassette_name = "openai_completion_async_39.yaml" if PY39 else "openai_completion_async.yaml"
    with request_vcr.use_cassette(cassette_name):
        await llm.agenerate(["Which team won the 2019 NBA finals?"])


@pytest.mark.snapshot(ignores=["meta.error.stack", "resource"])
def test_openai_llm_error(langchain, request_vcr):
    import openai  # Imported here because the os env OPENAI_API_KEY needs to be set via langchain fixture before import

    llm = langchain.llms.OpenAI(model="text-davinci-003")

    if parse_version(openai.__version__) >= (1, 0, 0):
        invalid_error = openai.BadRequestError
    else:
        invalid_error = openai.InvalidRequestError
    with pytest.raises(invalid_error):
        with request_vcr.use_cassette("openai_completion_error.yaml"):
            llm.generate([12345, 123456])


@pytest.mark.snapshot(ignores=["resource"])
def test_cohere_llm_sync(langchain, request_vcr):
    llm = langchain.llms.Cohere(cohere_api_key=os.getenv("COHERE_API_KEY", "<not-a-real-key>"))
    with request_vcr.use_cassette("cohere_completion_sync.yaml"):
        llm("What is the secret Krabby Patty recipe?")


@pytest.mark.snapshot(ignores=["resource"])
def test_huggingfacehub_llm_sync(langchain, request_vcr):
    llm = langchain.llms.HuggingFaceHub(
        repo_id="google/flan-t5-xxl",
        model_kwargs={"temperature": 0.5, "max_length": 256},
        huggingfacehub_api_token=os.getenv("HUGGINGFACEHUB_API_TOKEN", "<not-a-real-key>"),
    )
    with request_vcr.use_cassette("huggingfacehub_completion_sync.yaml"):
        llm("Why does Mr. Krabs have a whale daughter?")


@pytest.mark.snapshot(ignores=["meta.langchain.response.completions.0.text", "resource"])
def test_ai21_llm_sync(langchain, request_vcr):
    llm = langchain.llms.AI21(ai21_api_key=os.getenv("AI21_API_KEY", "<not-a-real-key>"))
    cassette_name = "ai21_completion_sync_39.yaml" if PY39 else "ai21_completion_sync.yaml"
    with request_vcr.use_cassette(cassette_name):
        llm("Why does everyone in Bikini Bottom hate Plankton?")


def test_openai_llm_metrics(langchain, request_vcr, mock_metrics, mock_logs, snapshot_tracer):
    llm = langchain.llms.OpenAI(model="text-davinci-003")
    cassette_name = "openai_completion_sync_39.yaml" if PY39 else "openai_completion_sync.yaml"
    with request_vcr.use_cassette(cassette_name):
        llm("Can you explain what Descartes meant by 'I think, therefore I am'?")
    expected_tags = [
        "version:",
        "env:",
        "service:",
        "langchain.request.provider:openai",
        "langchain.request.model:text-davinci-003",
        "langchain.request.type:llm",
        "langchain.request.api_key:...key>",
        "error:0",
    ]
    mock_metrics.assert_has_calls(
        [
            mock.call.distribution("tokens.prompt", 17, tags=expected_tags),
            mock.call.distribution("tokens.completion", mock.ANY, tags=expected_tags),
            mock.call.distribution("tokens.total", mock.ANY, tags=expected_tags),
            mock.call.increment("tokens.total_cost", mock.ANY, tags=expected_tags),
            mock.call.distribution("request.duration", mock.ANY, tags=expected_tags),
        ],
        any_order=True,
    )
    mock_logs.assert_not_called()


@pytest.mark.parametrize(
    "ddtrace_config_langchain",
    [dict(metrics_enabled=False, logs_enabled=True, log_prompt_completion_sample_rate=1.0)],
)
def test_llm_logs(langchain, ddtrace_config_langchain, request_vcr, mock_logs, mock_metrics, mock_tracer):
    llm = langchain.llms.OpenAI(model="text-davinci-003")
    cassette_name = "openai_completion_sync_39.yaml" if PY39 else "openai_completion_sync.yaml"
    with request_vcr.use_cassette(cassette_name):
        llm("Can you explain what Descartes meant by 'I think, therefore I am'?")
    span = mock_tracer.pop_traces()[0][0]
    trace_id, span_id = span.trace_id, span.span_id

    assert mock_logs.enqueue.call_count == 1
    mock_logs.enqueue.assert_called_with(
        {
            "timestamp": mock.ANY,
            "message": "sampled langchain.llms.openai.OpenAI",
            "hostname": mock.ANY,
            "ddsource": "langchain",
            "service": "",
            "status": "info",
            "ddtags": "env:,version:,langchain.request.provider:openai,langchain.request.model:text-davinci-003,langchain.request.type:llm,langchain.request.api_key:...key>",  # noqa: E501
            "dd.trace_id": hex(trace_id)[2:],
            "dd.span_id": str(span_id),
            "prompts": ["Can you explain what Descartes meant by 'I think, therefore I am'?"],
            "choices": mock.ANY,
        }
    )
    mock_metrics.increment.assert_not_called()
    mock_metrics.distribution.assert_not_called()
    mock_metrics.count.assert_not_called()


@pytest.mark.skipif(PY39, reason="Python 3.10+ specific test")
@pytest.mark.snapshot(
    token="tests.contrib.langchain.test_langchain.test_openai_chat_model_call",
    ignores=["metrics.langchain.tokens.total_cost", "resource"],
)
def test_openai_chat_model_sync_call(langchain, request_vcr):
    chat = langchain.chat_models.ChatOpenAI(temperature=0, max_tokens=256)
    with request_vcr.use_cassette("openai_chat_completion_sync_call.yaml"):
        chat(messages=[langchain.schema.HumanMessage(content="When do you use 'whom' instead of 'who'?")])


@pytest.mark.skipif(not PY39, reason="Python 3.9 specific test")
@pytest.mark.snapshot(ignores=["metrics.langchain.tokens.total_cost"])
def test_openai_chat_model_sync_call_39(langchain, request_vcr):
    chat = langchain.chat_models.ChatOpenAI(temperature=0, max_tokens=256)
    with request_vcr.use_cassette("openai_chat_completion_sync_call_39.yaml"):
        chat([langchain.schema.HumanMessage(content="When do you use 'whom' instead of 'who'?")])


@pytest.mark.skipif(PY39, reason="Python 3.10+ specific test")
@pytest.mark.snapshot(
    token="tests.contrib.langchain.test_langchain.test_openai_chat_model_generate",
    ignores=["metrics.langchain.tokens.total_cost", "resource"],
)
def test_openai_chat_model_sync_generate(langchain, request_vcr):
    chat = langchain.chat_models.ChatOpenAI(temperature=0, max_tokens=256)
    with request_vcr.use_cassette("openai_chat_completion_sync_generate.yaml"):
        chat.generate(
            [
                [
                    langchain.schema.SystemMessage(content="Respond like a frat boy."),
                    langchain.schema.HumanMessage(
                        content="Where's the nearest equinox gym from Hudson Yards manhattan?"
                    ),
                ],
                [
                    langchain.schema.SystemMessage(content="Respond with a pirate accent."),
                    langchain.schema.HumanMessage(content="How does one get to Bikini Bottom from New York?"),
                ],
            ]
        )


@pytest.mark.skipif(not PY39, reason="Python 3.9 specific test")
@pytest.mark.snapshot(ignores=["metrics.langchain.tokens.total_cost"])
def test_openai_chat_model_sync_generate_39(langchain, request_vcr):
    chat = langchain.chat_models.ChatOpenAI(temperature=0, max_tokens=256)
    with request_vcr.use_cassette("openai_chat_completion_sync_generate_39.yaml"):
        chat.generate(
            [
                [
                    langchain.schema.SystemMessage(content="Respond like a frat boy."),
                    langchain.schema.HumanMessage(
                        content="Where's the nearest equinox gym from Hudson Yards manhattan?"
                    ),
                ],
                [
                    langchain.schema.SystemMessage(content="Respond with a pirate accent."),
                    langchain.schema.HumanMessage(content="How does one get to Bikini Bottom from New York?"),
                ],
            ]
        )


@pytest.mark.asyncio
@pytest.mark.snapshot(
    token="tests.contrib.langchain.test_langchain.test_openai_chat_model_call",
    ignores=["metrics.langchain.tokens.total_cost", "resource"],
)
async def test_openai_chat_model_async_call(langchain, request_vcr):
    chat = langchain.chat_models.ChatOpenAI(temperature=0, max_tokens=256)
    with request_vcr.use_cassette("openai_chat_completion_async_call.yaml"):
        await chat._call_async([langchain.schema.HumanMessage(content="When do you use 'whom' instead of 'who'?")])


@pytest.mark.asyncio
@pytest.mark.snapshot(
    token="tests.contrib.langchain.test_langchain.test_openai_chat_model_generate",
    ignores=["metrics.langchain.tokens.total_cost", "resource"],
)
async def test_openai_chat_model_async_generate(langchain, request_vcr):
    chat = langchain.chat_models.ChatOpenAI(temperature=0, max_tokens=256)
    with request_vcr.use_cassette("openai_chat_completion_async_generate.yaml"):
        await chat.agenerate(
            [
                [
                    langchain.schema.SystemMessage(content="Respond like a frat boy."),
                    langchain.schema.HumanMessage(
                        content="Where's the nearest equinox gym from Hudson Yards manhattan?"
                    ),
                ],
                [
                    langchain.schema.SystemMessage(content="Respond with a pirate accent."),
                    langchain.schema.HumanMessage(content="How does one get to Bikini Bottom from New York?"),
                ],
            ]
        )


def test_chat_model_metrics(langchain, request_vcr, mock_metrics, mock_logs, snapshot_tracer):
    chat = langchain.chat_models.ChatOpenAI(temperature=0, max_tokens=256)
    cassette_name = "openai_chat_completion_sync_call_39.yaml" if PY39 else "openai_chat_completion_sync_call.yaml"
    with request_vcr.use_cassette(cassette_name):
        chat([langchain.schema.HumanMessage(content="When do you use 'whom' instead of 'who'?")])
    expected_tags = [
        "version:",
        "env:",
        "service:",
        "langchain.request.provider:openai",
        "langchain.request.model:gpt-3.5-turbo",
        "langchain.request.type:chat_model",
        "langchain.request.api_key:...key>",
        "error:0",
    ]
    mock_metrics.assert_has_calls(
        [
            mock.call.distribution("tokens.prompt", 21, tags=expected_tags),
            mock.call.distribution("tokens.completion", 59, tags=expected_tags),
            mock.call.distribution("tokens.total", 80, tags=expected_tags),
            mock.call.increment("tokens.total_cost", mock.ANY, tags=expected_tags),
            mock.call.distribution("request.duration", mock.ANY, tags=expected_tags),
        ],
        any_order=True,
    )
    mock_logs.assert_not_called()


@pytest.mark.parametrize(
    "ddtrace_config_langchain",
    [dict(metrics_enabled=False, logs_enabled=True, log_prompt_completion_sample_rate=1.0)],
)
def test_chat_model_logs(langchain, ddtrace_config_langchain, request_vcr, mock_logs, mock_metrics, mock_tracer):
    chat = langchain.chat_models.ChatOpenAI(temperature=0, max_tokens=256)
    cassette_name = "openai_chat_completion_sync_call_39.yaml" if PY39 else "openai_chat_completion_sync_call.yaml"
    with request_vcr.use_cassette(cassette_name):
        chat([langchain.schema.HumanMessage(content="When do you use 'whom' instead of 'who'?")])
    span = mock_tracer.pop_traces()[0][0]
    trace_id, span_id = span.trace_id, span.span_id

    assert mock_logs.enqueue.call_count == 1
    mock_logs.enqueue.assert_called_with(
        {
            "timestamp": mock.ANY,
            "message": "sampled langchain.chat_models.openai.ChatOpenAI",
            "hostname": mock.ANY,
            "ddsource": "langchain",
            "service": "",
            "status": "info",
            "ddtags": "env:,version:,langchain.request.provider:openai,langchain.request.model:gpt-3.5-turbo,langchain.request.type:chat_model,langchain.request.api_key:...key>",  # noqa: E501
            "dd.trace_id": hex(trace_id)[2:],
            "dd.span_id": str(span_id),
            "messages": [[{"content": "When do you use 'whom' instead of 'who'?", "message_type": "HumanMessage"}]],
            "choices": [[{"content": mock.ANY, "message_type": "AIMessage"}]],
        }
    )
    mock_metrics.increment.assert_not_called()
    mock_metrics.distribution.assert_not_called()
    mock_metrics.count.assert_not_called()


@pytest.mark.snapshot
def test_openai_embedding_query(langchain, request_vcr):
    embeddings = langchain.embeddings.OpenAIEmbeddings()
    cassette_name = "openai_embedding_query_39.yaml" if PY39 else "openai_embedding_query.yaml"
    with request_vcr.use_cassette(cassette_name):
        embeddings.embed_query("this is a test query.")


@pytest.mark.skip(reason="Tiktoken request to get model encodings cannot be made in CI")
@pytest.mark.snapshot
def test_openai_embedding_document(langchain, request_vcr):
    embeddings = langchain.embeddings.OpenAIEmbeddings()
    cassette_name = "openai_embedding_document_39.yaml" if PY39 else "openai_embedding_document.yaml"
    with request_vcr.use_cassette(cassette_name):
        embeddings.embed_documents(["this is", "a test document."])


@pytest.mark.snapshot(ignores=["resource"])
def test_fake_embedding_query(langchain):
    embeddings = langchain.embeddings.FakeEmbeddings(size=99)
    embeddings.embed_query(text="foo")


@pytest.mark.snapshot(ignores=["resource"])
def test_fake_embedding_document(langchain):
    embeddings = langchain.embeddings.FakeEmbeddings(size=99)
    embeddings.embed_documents(texts=["foo", "bar"])


def test_openai_embedding_metrics(langchain, request_vcr, mock_metrics, mock_logs, snapshot_tracer):
    embeddings = langchain.embeddings.OpenAIEmbeddings()
    cassette_name = "openai_embedding_query_39.yaml" if PY39 else "openai_embedding_query.yaml"
    with request_vcr.use_cassette(cassette_name):
        embeddings.embed_query("this is a test query.")
    expected_tags = [
        "version:",
        "env:",
        "service:",
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
def test_embedding_logs(langchain, ddtrace_config_langchain, request_vcr, mock_logs, mock_metrics, mock_tracer):
    embeddings = langchain.embeddings.OpenAIEmbeddings()
    cassette_name = "openai_embedding_query_39.yaml" if PY39 else "openai_embedding_query.yaml"
    with request_vcr.use_cassette(cassette_name):
        embeddings.embed_query("this is a test query.")
    span = mock_tracer.pop_traces()[0][0]
    trace_id, span_id = span.trace_id, span.span_id

    assert mock_logs.enqueue.call_count == 1
    mock_logs.enqueue.assert_called_with(
        {
            "timestamp": mock.ANY,
            "message": "sampled langchain.embeddings.openai.OpenAIEmbeddings",
            "hostname": mock.ANY,
            "ddsource": "langchain",
            "service": "",
            "status": "info",
            "ddtags": "env:,version:,langchain.request.provider:openai,langchain.request.model:text-embedding-ada-002,langchain.request.type:embedding,langchain.request.api_key:...key>",  # noqa: E501
            "dd.trace_id": hex(trace_id)[2:],
            "dd.span_id": str(span_id),
            "inputs": ["this is a test query."],
        }
    )
    mock_metrics.increment.assert_not_called()
    mock_metrics.distribution.assert_not_called()
    mock_metrics.count.assert_not_called()


@pytest.mark.snapshot(
    token="tests.contrib.langchain.test_langchain.test_openai_math_chain",
    ignores=["metrics.langchain.tokens.total_cost", "resource"],
)
def test_openai_math_chain_sync(langchain, request_vcr):
    """
    Test that using the provided LLMMathChain will result in a 3-span trace with
    the overall LLMMathChain, LLMChain, and underlying OpenAI interface.
    """
    chain = langchain.chains.LLMMathChain(llm=langchain.llms.OpenAI(temperature=0))
    cassette_name = "openai_math_chain_sync_39.yaml" if PY39 else "openai_math_chain_sync.yaml"
    with request_vcr.use_cassette(cassette_name):
        chain.run("what is two raised to the fifty-fourth power?")


@pytest.mark.asyncio
@pytest.mark.snapshot(
    token="tests.contrib.langchain.test_langchain.test_openai_math_chain",
    ignores=["metrics.langchain.tokens.total_cost"],
)
async def test_openai_math_chain_async(langchain, request_vcr):
    """
    Test that using the provided LLMMathChain will result in a 3-span trace with
    the overall LLMMathChain, LLMChain, and underlying OpenAI interface.
    """
    chain = langchain.chains.LLMMathChain(llm=langchain.llms.OpenAI(temperature=0))
    with request_vcr.use_cassette("openai_math_chain_async.yaml"):
        await chain.acall("what is two raised to the fifty-fourth power?")


@pytest.mark.snapshot(token="tests.contrib.langchain.test_langchain.test_cohere_math_chain")
def test_cohere_math_chain_sync(langchain, request_vcr):
    """
    Test that using the provided LLMMathChain will result in a 3-span trace with
    the overall LLMMathChain, LLMChain, and underlying Cohere interface.
    """
    chain = langchain.chains.LLMMathChain(
        llm=langchain.llms.Cohere(cohere_api_key=os.getenv("COHERE_API_KEY", "<not-a-real-key>"))
    )
    with request_vcr.use_cassette("cohere_math_chain_sync.yaml"):
        chain.run("what is thirteen raised to the .3432 power?")


@pytest.mark.skipif(PY39, reason="Requires unnecessary cassette file for Python 3.9")
@pytest.mark.snapshot(
    token="tests.contrib.langchain.test_langchain.test_openai_sequential_chain",
    ignores=["metrics.langchain.tokens.total_cost", "resource"],
)
def test_openai_sequential_chain(langchain, request_vcr):
    """
    Test that using a SequentialChain will result in a 4-span trace with
    the overall SequentialChain, TransformChain, LLMChain, and underlying OpenAI interface.
    """

    def _transform_func(inputs):
        """Helper function to replace multiple new lines and multiple spaces with a single space"""
        text = inputs["text"]
        text = re.sub(r"(\r\n|\r|\n){2,}", r"\n", text)
        text = re.sub(r"[ \t]+", " ", text)
        return {"output_text": text}

    clean_extra_spaces_chain = langchain.chains.TransformChain(
        input_variables=["text"], output_variables=["output_text"], transform=_transform_func
    )
    template = """Paraphrase this text:

        {output_text}

        In the style of a {style}.

        Paraphrase: """
    prompt = langchain.PromptTemplate(input_variables=["style", "output_text"], template=template)
    style_paraphrase_chain = langchain.chains.LLMChain(
        llm=langchain.llms.OpenAI(), prompt=prompt, output_key="final_output"
    )
    sequential_chain = langchain.chains.SequentialChain(
        chains=[clean_extra_spaces_chain, style_paraphrase_chain],
        input_variables=["text", "style"],
        output_variables=["final_output"],
    )

    input_text = """
        Chains allow us to combine multiple


        components together to create a single, coherent application.

        For example, we can create a chain that takes user input,       format it with a PromptTemplate,

        and then passes the formatted response to an LLM. We can build more complex chains by combining

        multiple chains together, or by


        combining chains with other components.
        """
    with request_vcr.use_cassette("openai_paraphrase.yaml"):
        sequential_chain.run({"text": input_text, "style": "a 90s rapper"})


@pytest.mark.skipif(PY39, reason="Requires unnecessary cassette file for Python 3.9")
@pytest.mark.snapshot(ignores=["langchain.tokens.total_cost", "resource"])
def test_openai_sequential_chain_with_multiple_llm_sync(langchain, request_vcr):
    template = """Paraphrase this text:

        {input_text}

        Paraphrase: """
    prompt = langchain.PromptTemplate(input_variables=["input_text"], template=template)
    style_paraphrase_chain = langchain.chains.LLMChain(
        llm=langchain.llms.OpenAI(), prompt=prompt, output_key="paraphrased_output"
    )
    rhyme_template = """Make this text rhyme:

        {paraphrased_output}

        Rhyme: """
    rhyme_prompt = langchain.PromptTemplate(input_variables=["paraphrased_output"], template=rhyme_template)
    rhyme_chain = langchain.chains.LLMChain(llm=langchain.llms.OpenAI(), prompt=rhyme_prompt, output_key="final_output")
    sequential_chain = langchain.chains.SequentialChain(
        chains=[style_paraphrase_chain, rhyme_chain],
        input_variables=["input_text"],
        output_variables=["final_output"],
    )

    with request_vcr.use_cassette("openai_sequential_paraphrase_and_rhyme_sync.yaml"):
        sequential_chain.run({"input_text": long_input_text})


@pytest.mark.asyncio
@pytest.mark.snapshot(ignores=["resource"])
async def test_openai_sequential_chain_with_multiple_llm_async(langchain, request_vcr):
    template = """Paraphrase this text:

        {input_text}

        Paraphrase: """
    prompt = langchain.PromptTemplate(input_variables=["input_text"], template=template)
    style_paraphrase_chain = langchain.chains.LLMChain(
        llm=langchain.llms.OpenAI(), prompt=prompt, output_key="paraphrased_output"
    )
    rhyme_template = """Make this text rhyme:

        {paraphrased_output}

        Rhyme: """
    rhyme_prompt = langchain.PromptTemplate(input_variables=["paraphrased_output"], template=rhyme_template)
    rhyme_chain = langchain.chains.LLMChain(llm=langchain.llms.OpenAI(), prompt=rhyme_prompt, output_key="final_output")
    sequential_chain = langchain.chains.SequentialChain(
        chains=[style_paraphrase_chain, rhyme_chain],
        input_variables=["input_text"],
        output_variables=["final_output"],
    )
    with request_vcr.use_cassette("openai_sequential_paraphrase_and_rhyme_async.yaml"):
        await sequential_chain.acall({"input_text": long_input_text})


def test_openai_chain_metrics(langchain, request_vcr, mock_metrics, mock_logs, snapshot_tracer):
    chain = langchain.chains.LLMMathChain(llm=langchain.llms.OpenAI(temperature=0))
    cassette_name = "openai_math_chain_sync_39.yaml" if PY39 else "openai_math_chain_sync.yaml"
    with request_vcr.use_cassette(cassette_name):
        chain.run("what is two raised to the fifty-fourth power?")
    expected_tags = [
        "version:",
        "env:",
        "service:",
        "langchain.request.provider:openai",
        "langchain.request.model:text-davinci-003",
        mock.ANY,  # should be in format "langchain.request.type:<type>"
        "langchain.request.api_key:...key>",
        "error:0",
    ]
    mock_metrics.assert_has_calls(
        [
            mock.call.distribution("tokens.prompt", 236, tags=expected_tags),
            mock.call.distribution("tokens.completion", 24, tags=expected_tags),
            mock.call.distribution("tokens.total", 260, tags=expected_tags),
            mock.call.increment("tokens.total_cost", mock.ANY, tags=expected_tags),
            mock.call.distribution("request.duration", mock.ANY, tags=expected_tags),
        ],
        any_order=True,
    )
    mock_logs.assert_not_called()


@pytest.mark.parametrize(
    "ddtrace_config_langchain",
    [dict(metrics_enabled=False, logs_enabled=True, log_prompt_completion_sample_rate=1.0)],
)
def test_chain_logs(langchain, ddtrace_config_langchain, request_vcr, mock_logs, mock_metrics, mock_tracer):
    chain = langchain.chains.LLMMathChain(llm=langchain.llms.OpenAI(temperature=0))
    cassette_name = "openai_math_chain_sync_39.yaml" if PY39 else "openai_math_chain_sync.yaml"
    with request_vcr.use_cassette(cassette_name):
        chain.run("what is two raised to the fifty-fourth power?")
    traces = mock_tracer.pop_traces()
    base_chain_span = traces[0][0]
    mid_chain_span = traces[0][1]
    llm_span = traces[0][2]

    assert mock_logs.enqueue.call_count == 3  # This operation includes 2 chains and 1 LLM call
    mock_logs.assert_has_calls(
        [
            mock.call.enqueue(
                {
                    "timestamp": mock.ANY,
                    "message": "sampled langchain.llms.openai.OpenAI",
                    "hostname": mock.ANY,
                    "ddsource": "langchain",
                    "service": "",
                    "status": "info",
                    "ddtags": "env:,version:,langchain.request.provider:openai,langchain.request.model:text-davinci-003,langchain.request.type:llm,langchain.request.api_key:...key>",  # noqa: E501
                    "dd.trace_id": hex(llm_span.trace_id)[2:],
                    "dd.span_id": str(llm_span.span_id),
                    "prompts": mock.ANY,
                    "choices": mock.ANY,
                }
            ),
            mock.call.enqueue(
                {
                    "timestamp": mock.ANY,
                    "message": "sampled langchain.chains.llm.LLMChain",
                    "hostname": mock.ANY,
                    "ddsource": "langchain",
                    "service": "",
                    "status": "info",
                    "ddtags": "env:,version:,langchain.request.provider:,langchain.request.model:,langchain.request.type:chain,langchain.request.api_key:",  # noqa: E501
                    "dd.trace_id": hex(mid_chain_span.trace_id)[2:],
                    "dd.span_id": str(mid_chain_span.span_id),
                    "inputs": mock.ANY,
                    "prompt": mock.ANY,
                    "outputs": {
                        "question": "what is two raised to the fifty-fourth power?",
                        "stop": mock.ANY,
                        "text": '\n```text\n2**54\n```\n...numexpr.evaluate("2**54")...\n',
                    },
                }
            ),
            mock.call.enqueue(
                {
                    "timestamp": mock.ANY,
                    "message": "sampled langchain.chains.llm_math.base.LLMMathChain",
                    "hostname": mock.ANY,
                    "ddsource": "langchain",
                    "service": "",
                    "status": "info",
                    "ddtags": "env:,version:,langchain.request.provider:,langchain.request.model:,langchain.request.type:chain,langchain.request.api_key:",  # noqa: E501
                    "dd.trace_id": hex(base_chain_span.trace_id)[2:],
                    "dd.span_id": str(base_chain_span.span_id),
                    "inputs": {"question": "what is two raised to the fifty-fourth power?"},
                    "prompt": mock.ANY,
                    "outputs": {
                        "question": "what is two raised to the fifty-fourth power?",
                        "answer": "Answer: 18014398509481984",
                    },
                }
            ),
        ]
    )
    mock_metrics.increment.assert_not_called()
    mock_metrics.distribution.assert_not_called()
    mock_metrics.count.assert_not_called()


def test_chat_prompt_template_does_not_parse_template(langchain, mock_tracer):
    """
    Test that tracing a chain with a ChatPromptTemplate does not try to directly parse the template,
    as ChatPromptTemplates do not contain a specific template attribute (which will lead to an attribute error)
    but instead contain multiple messages each with their own prompt template and are not trivial to tag.
    """
    import langchain.prompts.chat  # noqa: F401

    with mock.patch("langchain.chat_models.openai.ChatOpenAI._generate", side_effect=Exception("Mocked Error")):
        with pytest.raises(Exception) as exc_info:
            chat = langchain.chat_models.ChatOpenAI(temperature=0)
            template = "You are a helpful assistant that translates english to pirate."
            system_message_prompt = langchain.prompts.chat.SystemMessagePromptTemplate.from_template(template)
            example_human = langchain.prompts.chat.HumanMessagePromptTemplate.from_template("Hi")
            example_ai = langchain.prompts.chat.AIMessagePromptTemplate.from_template("Argh me mateys")
            human_template = "{text}"
            human_message_prompt = langchain.prompts.chat.HumanMessagePromptTemplate.from_template(human_template)
            chat_prompt = langchain.prompts.chat.ChatPromptTemplate.from_messages(
                [system_message_prompt, example_human, example_ai, human_message_prompt]
            )
            chain = langchain.chains.LLMChain(llm=chat, prompt=chat_prompt)
            chain.run("I love programming.")
        assert str(exc_info.value) == "Mocked Error"
    traces = mock_tracer.pop_traces()
    chain_span = traces[0][0]
    assert chain_span.get_tag("langchain.request.inputs.text") == "I love programming."
    assert chain_span.get_tag("langchain.request.type") == "chain"
    assert chain_span.get_tag("langchain.request.prompt") is None


@pytest.mark.snapshot
def test_pinecone_vectorstore_similarity_search(langchain, request_vcr):
    """
    Test that calling a similarity search on a Pinecone vectorstore with langchain will
    result in a 2-span trace with a vectorstore span and underlying OpenAI embedding interface span.
    """
    import pinecone

    cassette_name = "openai_pinecone_similarity_search_39.yaml" if PY39 else "openai_pinecone_similarity_search.yaml"
    with request_vcr.use_cassette(cassette_name):
        pinecone.init(
            api_key=os.getenv("PINECONE_API_KEY", "<not-a-real-key>"),
            environment=os.getenv("PINECONE_ENV", "<not-a-real-env>"),
        )
        embed = langchain.embeddings.OpenAIEmbeddings(model="text-embedding-ada-002")
        index = pinecone.Index(index_name="langchain-retrieval")
        vectorstore = langchain.vectorstores.Pinecone(index, embed.embed_query, "text")
        vectorstore.similarity_search("Who was Alan Turing?", 1)


@pytest.mark.skipif(PY39, reason="Cassette specific to Python 3.10+")
@pytest.mark.snapshot
def test_pinecone_vectorstore_retrieval_chain(langchain, request_vcr):
    """
    Test that calling a similarity search on a Pinecone vectorstore with langchain will
    result in a 2-span trace with a vectorstore span and underlying OpenAI embedding interface span.
    """
    import pinecone

    with request_vcr.use_cassette("openai_pinecone_vectorstore_retrieval_chain.yaml"):
        pinecone.init(
            api_key=os.getenv("PINECONE_API_KEY", "<not-a-real-key>"),
            environment=os.getenv("PINECONE_ENV", "<not-a-real-env>"),
        )
        embed = langchain.embeddings.OpenAIEmbeddings()
        index = pinecone.Index(index_name="langchain-retrieval")
        vectorstore = langchain.vectorstores.Pinecone(index, embed.embed_query, "text")

        llm = langchain.llms.OpenAI()
        qa_with_sources = langchain.chains.RetrievalQAWithSourcesChain.from_chain_type(
            llm=llm, chain_type="stuff", retriever=vectorstore.as_retriever()
        )
        qa_with_sources("Who was Alan Turing?")


@pytest.mark.skipif(not PY39, reason="Cassette specific to Python 3.9")
@pytest.mark.snapshot
def test_pinecone_vectorstore_retrieval_chain_39(langchain, request_vcr):
    """
    Test that calling a similarity search on a Pinecone vectorstore with langchain will
    result in a 2-span trace with a vectorstore span and underlying OpenAI embedding interface span.
    """
    import pinecone

    with request_vcr.use_cassette("openai_pinecone_vectorstore_retrieval_chain_39.yaml"):
        pinecone.init(
            api_key=os.getenv("PINECONE_API_KEY", "<not-a-real-key>"),
            environment=os.getenv("PINECONE_ENV", "<not-a-real-env>"),
        )
        embed = langchain.embeddings.OpenAIEmbeddings(model="text-embedding-ada-002")
        index = pinecone.Index(index_name="langchain-retrieval")
        vectorstore = langchain.vectorstores.Pinecone(index, embed.embed_query, "text")

        llm = langchain.llms.OpenAI()
        qa_with_sources = langchain.chains.RetrievalQAWithSourcesChain.from_chain_type(
            llm=llm, chain_type="stuff", retriever=vectorstore.as_retriever()
        )
        qa_with_sources("Who was Alan Turing?")


def test_vectorstore_similarity_search_metrics(langchain, request_vcr, mock_metrics, mock_logs, snapshot_tracer):
    import pinecone

    cassette_name = "openai_pinecone_similarity_search_39.yaml" if PY39 else "openai_pinecone_similarity_search.yaml"
    with request_vcr.use_cassette(cassette_name):
        pinecone.init(
            api_key=os.getenv("PINECONE_API_KEY", "<not-a-real-key>"),
            environment=os.getenv("PINECONE_ENV", "<not-a-real-env>"),
        )
        embed = langchain.embeddings.OpenAIEmbeddings(model="text-embedding-ada-002")
        index = pinecone.Index(index_name="langchain-retrieval")
        vectorstore = langchain.vectorstores.Pinecone(index, embed.embed_query, "text")
        vectorstore.similarity_search("Who was Alan Turing?", 1)
    expected_tags = [
        "version:",
        "env:",
        "service:",
        "langchain.request.provider:pinecone",
        "langchain.request.model:",
        "langchain.request.type:similarity_search",
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
def test_vectorstore_logs(langchain, ddtrace_config_langchain, request_vcr, mock_logs, mock_metrics, mock_tracer):
    import pinecone

    cassette_name = "openai_pinecone_similarity_search_39.yaml" if PY39 else "openai_pinecone_similarity_search.yaml"
    with request_vcr.use_cassette(cassette_name):
        pinecone.init(
            api_key=os.getenv("PINECONE_API_KEY", "<not-a-real-key>"),
            environment=os.getenv("PINECONE_ENV", "<not-a-real-env>"),
        )
        embed = langchain.embeddings.OpenAIEmbeddings(model="text-embedding-ada-002")
        index = pinecone.Index(index_name="langchain-retrieval")
        vectorstore = langchain.vectorstores.Pinecone(index, embed.embed_query, "text")
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
                    "message": "sampled langchain.embeddings.openai.OpenAIEmbeddings",
                    "hostname": mock.ANY,
                    "ddsource": "langchain",
                    "service": "",
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
                    "message": "sampled langchain.vectorstores.pinecone.Pinecone",
                    "hostname": mock.ANY,
                    "ddsource": "langchain",
                    "service": "",
                    "status": "info",
                    "ddtags": "env:,version:,langchain.request.provider:pinecone,langchain.request.model:,langchain.request.type:similarity_search,langchain.request.api_key:...key>",  # noqa: E501
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


@pytest.mark.skipif(PY39, reason="Requires unnecessary cassette file for Python 3.9")
@pytest.mark.snapshot(ignores=["metrics.langchain.tokens.total_cost", "resource"])
def test_openai_integration(langchain, request_vcr, ddtrace_run_python_code_in_subprocess):
    env = os.environ.copy()
    pypath = [os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))]
    if "PYTHONPATH" in env:
        pypath.append(env["PYTHONPATH"])
    env.update(
        {
            "PYTHONPATH": ":".join(pypath),
            # Disable metrics because the test agent doesn't support metrics
            "DD_LANGCHAIN_METRICS_ENABLED": "false",
            "DD_OPENAI_METRICS_ENABLED": "false",
            "OPENAI_API_KEY": "<not-a-real-key>",
        }
    )
    out, err, status, pid = ddtrace_run_python_code_in_subprocess(
        """
from langchain.llms import OpenAI
import ddtrace
from tests.contrib.langchain.test_langchain import get_request_vcr
llm = OpenAI()
with get_request_vcr(subdirectory_name="langchain").use_cassette("openai_completion_sync.yaml"):
    llm("Can you explain what Descartes meant by 'I think, therefore I am'?")
""",
        env=env,
    )
    assert status == 0, err
    assert out == b""
    assert err == b""


@pytest.mark.skipif(PY39, reason="Requires unnecessary cassette file for Python 3.9")
@pytest.mark.snapshot(ignores=["metrics.langchain.tokens.total_cost", "resource"])
@pytest.mark.parametrize("schema_version", [None, "v0", "v1"])
@pytest.mark.parametrize("service_name", [None, "mysvc"])
def test_openai_service_name(
    langchain, request_vcr, ddtrace_run_python_code_in_subprocess, schema_version, service_name
):
    env = os.environ.copy()
    pypath = [os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))]
    if "PYTHONPATH" in env:
        pypath.append(env["PYTHONPATH"])
    env.update(
        {
            "PYTHONPATH": ":".join(pypath),
            # Disable metrics because the test agent doesn't support metrics
            "DD_LANGCHAIN_METRICS_ENABLED": "false",
            "DD_OPENAI_METRICS_ENABLED": "false",
            "OPENAI_API_KEY": "<not-a-real-key>",
        }
    )
    if service_name:
        env["DD_SERVICE"] = service_name
    if schema_version:
        env["DD_TRACE_SPAN_ATTRIBUTE_SCHEMA"] = schema_version
    out, err, status, pid = ddtrace_run_python_code_in_subprocess(
        # TODO: need to correct this
        """
from langchain.llms import OpenAI
import ddtrace
from tests.contrib.langchain.test_langchain import get_request_vcr
llm = OpenAI()
with get_request_vcr(subdirectory_name="langchain").use_cassette("openai_completion_sync.yaml"):
    llm("Can you explain what Descartes meant by 'I think, therefore I am'?")
""",
        env=env,
    )
    assert status == 0, err
    assert out == b""
    assert err == b""


@pytest.mark.parametrize(
    "ddtrace_config_langchain",
    [dict(metrics_enabled=False, logs_enabled=True, log_prompt_completion_sample_rate=1.0)],
)
def test_llm_logs_when_response_not_completed(
    langchain, ddtrace_config_langchain, mock_logs, mock_metrics, mock_tracer
):
    """Test that errors get logged even if the response is not returned."""
    with mock.patch("langchain.llms.openai.OpenAI._generate", side_effect=Exception("Mocked Error")):
        with pytest.raises(Exception) as exc_info:
            llm = langchain.llms.OpenAI(model="text-davinci-003")
            llm("Can you please not return an error?")
        assert str(exc_info.value) == "Mocked Error"
    span = mock_tracer.pop_traces()[0][0]
    trace_id, span_id = span.trace_id, span.span_id

    assert mock_logs.enqueue.call_count == 1
    mock_logs.enqueue.assert_called_with(
        {
            "timestamp": mock.ANY,
            "message": "sampled langchain.llms.openai.OpenAI",
            "hostname": mock.ANY,
            "ddsource": "langchain",
            "service": "",
            "status": "error",
            "ddtags": "env:,version:,langchain.request.provider:openai,langchain.request.model:text-davinci-003,langchain.request.type:llm,langchain.request.api_key:...key>",  # noqa: E501
            "dd.trace_id": hex(trace_id)[2:],
            "dd.span_id": str(span_id),
            "prompts": ["Can you please not return an error?"],
            "choices": [],
        }
    )


@pytest.mark.parametrize(
    "ddtrace_config_langchain",
    [dict(metrics_enabled=False, logs_enabled=True, log_prompt_completion_sample_rate=1.0)],
)
def test_chat_model_logs_when_response_not_completed(
    langchain, ddtrace_config_langchain, mock_logs, mock_metrics, mock_tracer
):
    """Test that errors get logged even if the response is not returned."""
    with mock.patch("langchain.chat_models.openai.ChatOpenAI._generate", side_effect=Exception("Mocked Error")):
        with pytest.raises(Exception) as exc_info:
            chat = langchain.chat_models.ChatOpenAI(temperature=0, max_tokens=256)
            chat([langchain.schema.HumanMessage(content="Can you please not return an error?")])
        assert str(exc_info.value) == "Mocked Error"
    span = mock_tracer.pop_traces()[0][0]
    trace_id, span_id = span.trace_id, span.span_id

    assert mock_logs.enqueue.call_count == 1
    mock_logs.enqueue.assert_called_with(
        {
            "timestamp": mock.ANY,
            "message": "sampled langchain.chat_models.openai.ChatOpenAI",
            "hostname": mock.ANY,
            "ddsource": "langchain",
            "service": "",
            "status": "error",
            "ddtags": "env:,version:,langchain.request.provider:openai,langchain.request.model:gpt-3.5-turbo,langchain.request.type:chat_model,langchain.request.api_key:...key>",  # noqa: E501
            "dd.trace_id": hex(trace_id)[2:],
            "dd.span_id": str(span_id),
            "messages": [[{"content": "Can you please not return an error?", "message_type": "HumanMessage"}]],
            "choices": [],
        }
    )


@pytest.mark.parametrize(
    "ddtrace_config_langchain",
    [dict(metrics_enabled=False, logs_enabled=True, log_prompt_completion_sample_rate=1.0)],
)
def test_embedding_logs_when_response_not_completed(
    langchain, ddtrace_config_langchain, mock_logs, mock_metrics, mock_tracer
):
    """Test that errors get logged even if the response is not returned."""
    with mock.patch(
        "langchain.embeddings.openai.OpenAIEmbeddings._embedding_func",
        side_effect=Exception("Mocked Error"),
    ):
        with pytest.raises(Exception) as exc_info:
            embeddings = langchain.embeddings.OpenAIEmbeddings()
            embeddings.embed_query("Can you please not return an error?")
        assert str(exc_info.value) == "Mocked Error"
    span = mock_tracer.pop_traces()[0][0]
    trace_id, span_id = span.trace_id, span.span_id

    assert mock_logs.enqueue.call_count == 1
    mock_logs.enqueue.assert_called_with(
        {
            "timestamp": mock.ANY,
            "message": "sampled langchain.embeddings.openai.OpenAIEmbeddings",
            "hostname": mock.ANY,
            "ddsource": "langchain",
            "service": "",
            "status": "error",
            "ddtags": "env:,version:,langchain.request.provider:openai,langchain.request.model:text-embedding-ada-002,langchain.request.type:embedding,langchain.request.api_key:...key>",  # noqa: E501
            "dd.trace_id": hex(trace_id)[2:],
            "dd.span_id": str(span_id),
            "inputs": ["Can you please not return an error?"],
        }
    )


@pytest.mark.parametrize(
    "ddtrace_config_langchain",
    [dict(metrics_enabled=False, logs_enabled=True, log_prompt_completion_sample_rate=1.0)],
)
def test_vectorstore_logs_error(langchain, ddtrace_config_langchain, mock_logs, mock_metrics, mock_tracer):
    """Test that errors get logged even if the response is not returned."""
    with mock.patch(
        "langchain.embeddings.openai.OpenAIEmbeddings._embedding_func",
        side_effect=Exception("Mocked Error"),
    ):
        with pytest.raises(Exception) as exc_info:
            import pinecone

            pinecone.init(
                api_key=os.getenv("PINECONE_API_KEY", "<not-a-real-key>"),
                environment=os.getenv("PINECONE_ENV", "<not-a-real-env>"),
            )
            embed = langchain.embeddings.OpenAIEmbeddings(
                model="text-embedding-ada-002", openai_api_key=os.getenv("OPENAI_API_KEY", "<not-a-real-key>")
            )
            index = pinecone.Index(index_name="langchain-retrieval")
            vectorstore = langchain.vectorstores.Pinecone(index, embed.embed_query, "text")
            vectorstore.similarity_search("Can you please not return an error?", 1)
        assert str(exc_info.value) == "Mocked Error"
    traces = mock_tracer.pop_traces()
    vectorstore_span = traces[0][0]
    assert mock_logs.enqueue.call_count == 2  # This operation includes 1 vectorstore call and 1 embeddings call
    mock_logs.enqueue.assert_called_with(
        {
            "timestamp": mock.ANY,
            "message": "sampled langchain.vectorstores.pinecone.Pinecone",
            "hostname": mock.ANY,
            "ddsource": "langchain",
            "service": "",
            "status": "error",
            "ddtags": "env:,version:,langchain.request.provider:pinecone,langchain.request.model:,langchain.request.type:similarity_search,langchain.request.api_key:...key>",  # noqa: E501
            "dd.trace_id": hex(vectorstore_span.trace_id)[2:],
            "dd.span_id": str(vectorstore_span.span_id),
            "query": "Can you please not return an error?",
            "k": 1,
            "documents": [],
        }
    )
