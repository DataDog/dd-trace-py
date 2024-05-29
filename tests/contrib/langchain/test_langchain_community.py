import json
from operator import itemgetter
import os
import re
import sys

import langchain
import langchain.prompts  # noqa: F401
import mock
import pytest

from ddtrace.internal.utils.version import parse_version
from ddtrace.llmobs import LLMObs
from tests.contrib.langchain.utils import get_request_vcr
from tests.llmobs._utils import _expected_llmobs_llm_span_event
from tests.llmobs._utils import _expected_llmobs_non_llm_span_event
from tests.subprocesstest import SubprocessTestCase
from tests.subprocesstest import run_in_subprocess
from tests.utils import flaky
from tests.utils import override_global_config


SHOULD_USE_LANGCHAIN_COMMUNITY = parse_version(langchain.__version__) >= (0, 1)
SHOULD_USE_LANGCHAIN_OPENAI = SHOULD_USE_LANGCHAIN_COMMUNITY

pytestmark = pytest.mark.skipif(
    not SHOULD_USE_LANGCHAIN_COMMUNITY or sys.version_info < (3, 10),
    reason="This module only tests langchain_community and Python 3.10+",
)


@pytest.fixture(scope="session")
def request_vcr():
    yield get_request_vcr(subdirectory_name="langchain_community")


@flaky(1735812000)
@pytest.mark.parametrize("ddtrace_config_langchain", [dict(logs_enabled=True, log_prompt_completion_sample_rate=1.0)])
def test_global_tags(
    ddtrace_config_langchain, langchain, langchain_openai, request_vcr, mock_metrics, mock_logs, mock_tracer
):
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


@flaky(1735812000)
@pytest.mark.snapshot(ignores=["metrics.langchain.tokens.total_cost", "resource"])
def test_openai_llm_sync(langchain, langchain_openai, request_vcr):
    llm = langchain_openai.OpenAI()
    with request_vcr.use_cassette("openai_completion_sync.yaml"):
        llm.invoke("Can you explain what Descartes meant by 'I think, therefore I am'?")


@flaky(1735812000)
@pytest.mark.snapshot
def test_openai_llm_sync_multiple_prompts(langchain, langchain_openai, request_vcr):
    llm = langchain_openai.OpenAI()
    with request_vcr.use_cassette("openai_completion_sync_multi_prompt.yaml"):
        llm.generate(
            prompts=[
                "What is the best way to teach a baby multiple languages?",
                "How many times has Spongebob failed his road test?",
            ]
        )


@pytest.mark.asyncio
@pytest.mark.snapshot
async def test_openai_llm_async(langchain, langchain_openai, request_vcr):
    llm = langchain_openai.OpenAI()
    with request_vcr.use_cassette("openai_completion_async.yaml"):
        await llm.agenerate(["Which team won the 2019 NBA finals?"])


@pytest.mark.snapshot
def test_openai_llm_sync_stream(langchain, langchain_openai, request_vcr):
    llm = langchain_openai.OpenAI(streaming=True)
    with request_vcr.use_cassette("openai_completion_sync_stream.yaml"):
        llm.invoke("Why is Spongebob so bad at driving?")


@pytest.mark.asyncio
@pytest.mark.snapshot
async def test_openai_llm_async_stream(langchain, langchain_openai, request_vcr):
    llm = langchain_openai.OpenAI(streaming=True)
    with request_vcr.use_cassette("openai_completion_async_stream.yaml"):
        await llm.agenerate(["Why is Spongebob so bad at driving?"])


@flaky(1735812000)
@pytest.mark.snapshot(ignores=["meta.error.stack", "resource"])
def test_openai_llm_error(langchain, langchain_openai, request_vcr):
    import openai  # Imported here because the os env OPENAI_API_KEY needs to be set via langchain fixture before import

    llm = langchain_openai.OpenAI()

    # if parse_version(openai.__version__) >= (1, 0, 0):
    if getattr(openai, "__version__", "") >= "1.0.0":
        invalid_error = openai.BadRequestError
    else:
        invalid_error = openai.InvalidRequestError
    with pytest.raises(invalid_error):
        with request_vcr.use_cassette("openai_completion_error.yaml"):
            llm.generate([12345, 123456])


@pytest.mark.snapshot
def test_cohere_llm_sync(langchain_community, request_vcr):
    llm = langchain_community.llms.Cohere(cohere_api_key=os.getenv("COHERE_API_KEY", "<not-a-real-key>"))
    with request_vcr.use_cassette("cohere_completion_sync.yaml"):
        llm.invoke("What is the secret Krabby Patty recipe?")


@pytest.mark.snapshot
def test_ai21_llm_sync(langchain, langchain_community, request_vcr):
    llm = langchain_community.llms.AI21(ai21_api_key=os.getenv("AI21_API_KEY", "<not-a-real-key>"))
    with request_vcr.use_cassette("ai21_completion_sync.yaml"):
        llm.invoke("Why does everyone in Bikini Bottom hate Plankton?")


def test_openai_llm_metrics(langchain, langchain_openai, request_vcr, mock_metrics, mock_logs, snapshot_tracer):
    llm = langchain_openai.OpenAI()
    with request_vcr.use_cassette("openai_completion_sync.yaml"):
        llm.invoke("Can you explain what Descartes meant by 'I think, therefore I am'?")
    expected_tags = [
        "version:",
        "env:",
        "service:",
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
def test_llm_logs(
    langchain, langchain_openai, ddtrace_config_langchain, request_vcr, mock_logs, mock_metrics, mock_tracer
):
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
                    "service": "",
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


@flaky(1735812000)
@pytest.mark.snapshot
def test_openai_chat_model_sync_call_langchain_openai(langchain, langchain_openai, request_vcr):
    chat = langchain_openai.ChatOpenAI(temperature=0, max_tokens=256)
    with request_vcr.use_cassette("openai_chat_completion_sync_call.yaml"):
        chat.invoke(input=[langchain.schema.HumanMessage(content="When do you use 'whom' instead of 'who'?")])


@flaky(1735812000)
@pytest.mark.snapshot
def test_openai_chat_model_sync_generate(langchain, langchain_openai, request_vcr):
    chat = langchain_openai.ChatOpenAI(temperature=0, max_tokens=256)
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


@pytest.mark.snapshot
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
                    langchain.schema.HumanMessage(
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
@pytest.mark.snapshot
async def test_openai_chat_model_async_call(langchain, langchain_openai, request_vcr):
    chat = langchain_openai.ChatOpenAI(temperature=0, max_tokens=256)
    with request_vcr.use_cassette("openai_chat_completion_async_call.yaml"):
        await chat._call_async([langchain.schema.HumanMessage(content="When do you use 'whom' instead of 'who'?")])


@pytest.mark.asyncio
@pytest.mark.snapshot
async def test_openai_chat_model_async_generate(langchain, langchain_openai, request_vcr):
    chat = langchain_openai.ChatOpenAI(temperature=0, max_tokens=256)
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


@pytest.mark.snapshot(token="tests.contrib.langchain.test_langchain_community.test_openai_chat_model_stream")
def test_openai_chat_model_sync_stream(langchain, langchain_openai, request_vcr):
    chat = langchain_openai.ChatOpenAI(streaming=True, temperature=0, max_tokens=256)
    with request_vcr.use_cassette("openai_chat_completion_sync_stream.yaml"):
        chat.invoke(input=[langchain.schema.HumanMessage(content="What is the secret Krabby Patty recipe?")])


@pytest.mark.asyncio
@pytest.mark.snapshot(token="tests.contrib.langchain.test_langchain_community.test_openai_chat_model_stream")
async def test_openai_chat_model_async_stream(langchain, langchain_openai, request_vcr):
    chat = langchain_openai.ChatOpenAI(streaming=True, temperature=0, max_tokens=256)
    with request_vcr.use_cassette("openai_chat_completion_async_stream.yaml"):
        await chat.agenerate([[langchain.schema.HumanMessage(content="What is the secret Krabby Patty recipe?")]])


@flaky(1735812000)
def test_chat_model_metrics(langchain, langchain_openai, request_vcr, mock_metrics, mock_logs, snapshot_tracer):
    chat = langchain_openai.ChatOpenAI(temperature=0, max_tokens=256)
    with request_vcr.use_cassette("openai_chat_completion_sync_call.yaml"):
        chat.invoke(input=[langchain.schema.HumanMessage(content="When do you use 'whom' instead of 'who'?")])
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
            mock.call.distribution("tokens.prompt", 20, tags=expected_tags),
            mock.call.distribution("tokens.completion", 96, tags=expected_tags),
            mock.call.distribution("tokens.total", 116, tags=expected_tags),
            mock.call.increment("tokens.total_cost", mock.ANY, tags=expected_tags),
            mock.call.distribution("request.duration", mock.ANY, tags=expected_tags),
        ],
        any_order=True,
    )
    mock_logs.assert_not_called()


@flaky(1735812000)
@pytest.mark.parametrize(
    "ddtrace_config_langchain",
    [dict(metrics_enabled=False, logs_enabled=True, log_prompt_completion_sample_rate=1.0)],
)
def test_chat_model_logs(
    langchain, langchain_openai, ddtrace_config_langchain, request_vcr, mock_logs, mock_metrics, mock_tracer
):
    chat = langchain_openai.ChatOpenAI(temperature=0, max_tokens=256)
    with request_vcr.use_cassette("openai_chat_completion_sync_call.yaml"):
        chat.invoke(input=[langchain.schema.HumanMessage(content="When do you use 'whom' instead of 'who'?")])
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
                    "service": "",
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
def test_fake_embedding_query(langchain, langchain_community):
    embeddings = langchain_community.embeddings.FakeEmbeddings(size=99)
    embeddings.embed_query(text="foo")


@pytest.mark.snapshot
def test_fake_embedding_document(langchain, langchain_community):
    embeddings = langchain_community.embeddings.FakeEmbeddings(size=99)
    embeddings.embed_documents(texts=["foo", "bar"])


def test_openai_embedding_metrics(langchain_openai, request_vcr, mock_metrics, mock_logs, snapshot_tracer):
    with mock.patch("langchain_openai.OpenAIEmbeddings._get_len_safe_embeddings", return_value=[0.0] * 1536):
        embeddings = langchain_openai.OpenAIEmbeddings()
        with request_vcr.use_cassette("openai_embedding_query.yaml"):
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
                    "service": "",
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


@flaky(1735812000)
@pytest.mark.snapshot
def test_openai_math_chain_sync(langchain, langchain_openai, request_vcr):
    """
    Test that using the provided LLMMathChain will result in a 3-span trace with
    the overall LLMMathChain, LLMChain, and underlying OpenAI interface.
    """
    chain = langchain.chains.LLMMathChain.from_llm(langchain_openai.OpenAI(temperature=0))
    with request_vcr.use_cassette("openai_math_chain_sync.yaml"):
        chain.invoke("what is two raised to the fifty-fourth power?")


@flaky(1735812000)
@pytest.mark.snapshot(token="tests.contrib.langchain.test_langchain_community.test_chain_invoke")
def test_chain_invoke_dict_input(langchain, langchain_openai, request_vcr):
    prompt_template = "what is {base} raised to the fifty-fourth power?"
    prompt = langchain.prompts.PromptTemplate(input_variables=["base"], template=prompt_template)
    chain = langchain.chains.LLMChain(llm=langchain_openai.OpenAI(temperature=0), prompt=prompt)
    with request_vcr.use_cassette("openai_math_chain_sync.yaml"):
        chain.invoke(input={"base": "two"})


@flaky(1735812000)
@pytest.mark.snapshot(token="tests.contrib.langchain.test_langchain_community.test_chain_invoke")
def test_chain_invoke_str_input(langchain, langchain_openai, request_vcr):
    prompt_template = "what is {base} raised to the fifty-fourth power?"
    prompt = langchain.prompts.PromptTemplate(input_variables=["base"], template=prompt_template)
    chain = langchain.chains.LLMChain(llm=langchain_openai.OpenAI(temperature=0), prompt=prompt)
    with request_vcr.use_cassette("openai_math_chain_sync.yaml"):
        chain.invoke("two")


@pytest.mark.asyncio
@pytest.mark.snapshot
async def test_openai_math_chain_async(langchain, langchain_openai, request_vcr):
    """
    Test that using the provided LLMMathChain will result in a 3-span trace with
    the overall LLMMathChain, LLMChain, and underlying OpenAI interface.
    """
    chain = langchain.chains.LLMMathChain.from_llm(langchain_openai.OpenAI(temperature=0))
    with request_vcr.use_cassette("openai_math_chain_async.yaml"):
        await chain.ainvoke("what is two raised to the fifty-fourth power?")


@pytest.mark.snapshot(token="tests.contrib.langchain.test_langchain_community.test_cohere_math_chain")
def test_cohere_math_chain_sync(langchain, langchain_community, request_vcr):
    """
    Test that using the provided LLMMathChain will result in a 3-span trace with
    the overall LLMMathChain, LLMChain, and underlying Cohere interface.
    """
    chain = langchain.chains.LLMMathChain.from_llm(
        langchain_community.llms.Cohere(cohere_api_key=os.getenv("COHERE_API_KEY", "<not-a-real-key>"))
    )
    with request_vcr.use_cassette("cohere_math_chain_sync.yaml"):
        chain.invoke("what is thirteen raised to the .3432 power?")


@pytest.mark.snapshot
def test_openai_sequential_chain(langchain, langchain_openai, request_vcr):
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
    prompt = langchain.prompts.PromptTemplate(input_variables=["style", "output_text"], template=template)
    style_paraphrase_chain = langchain.chains.LLMChain(
        llm=langchain_openai.OpenAI(), prompt=prompt, output_key="final_output"
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
        sequential_chain.invoke({"text": input_text, "style": "a 90s rapper"})


@flaky(1735812000)
@pytest.mark.snapshot
def test_openai_sequential_chain_with_multiple_llm_sync(langchain, langchain_openai, request_vcr):
    template = """Paraphrase this text:

        {input_text}

        Paraphrase: """
    prompt = langchain.prompts.PromptTemplate(input_variables=["input_text"], template=template)
    style_paraphrase_chain = langchain.chains.LLMChain(
        llm=langchain_openai.OpenAI(), prompt=prompt, output_key="paraphrased_output"
    )
    rhyme_template = """Make this text rhyme:

        {paraphrased_output}

        Rhyme: """
    rhyme_prompt = langchain.prompts.PromptTemplate(input_variables=["paraphrased_output"], template=rhyme_template)
    rhyme_chain = langchain.chains.LLMChain(
        llm=langchain_openai.OpenAI(), prompt=rhyme_prompt, output_key="final_output"
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
    with request_vcr.use_cassette("openai_sequential_paraphrase_and_rhyme_sync.yaml"):
        sequential_chain.invoke({"input_text": input_text})


@pytest.mark.asyncio
@pytest.mark.snapshot
async def test_openai_sequential_chain_with_multiple_llm_async(langchain, langchain_openai, request_vcr):
    template = """Paraphrase this text:

        {input_text}

        Paraphrase: """
    prompt = langchain.prompts.PromptTemplate(input_variables=["input_text"], template=template)
    style_paraphrase_chain = langchain.chains.LLMChain(
        llm=langchain_openai.OpenAI(), prompt=prompt, output_key="paraphrased_output"
    )
    rhyme_template = """Make this text rhyme:

        {paraphrased_output}

        Rhyme: """
    rhyme_prompt = langchain.prompts.PromptTemplate(input_variables=["paraphrased_output"], template=rhyme_template)
    rhyme_chain = langchain.chains.LLMChain(
        llm=langchain_openai.OpenAI(), prompt=rhyme_prompt, output_key="final_output"
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
    with request_vcr.use_cassette("openai_sequential_paraphrase_and_rhyme_async.yaml"):
        await sequential_chain.ainvoke({"input_text": input_text})


@flaky(1735812000)
@pytest.mark.parametrize(
    "ddtrace_config_langchain",
    [dict(metrics_enabled=False, logs_enabled=True, log_prompt_completion_sample_rate=1.0)],
)
def test_chain_logs(
    langchain, langchain_openai, ddtrace_config_langchain, request_vcr, mock_logs, mock_metrics, mock_tracer
):
    chain = langchain.chains.LLMMathChain.from_llm(langchain_openai.OpenAI(temperature=0))
    with request_vcr.use_cassette("openai_math_chain_sync.yaml"):
        chain.invoke("what is two raised to the fifty-fourth power?")
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
                    "message": "sampled langchain_openai.llms.base.OpenAI",
                    "hostname": mock.ANY,
                    "ddsource": "langchain",
                    "service": "",
                    "status": "info",
                    "ddtags": "env:,version:,langchain.request.provider:openai,langchain.request.model:gpt-3.5-turbo-instruct,langchain.request.type:llm,langchain.request.api_key:...key>",  # noqa: E501
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
                        "text": '```text\n2**54\n```\n...numexpr.evaluate("2**54")...\n',
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


def test_chat_prompt_template_does_not_parse_template(langchain, langchain_openai, mock_tracer):
    """
    Test that tracing a chain with a ChatPromptTemplate does not try to directly parse the template,
    as ChatPromptTemplates do not contain a specific template attribute (which will lead to an attribute error)
    but instead contain multiple messages each with their own prompt template and are not trivial to tag.
    """
    import langchain.prompts.chat  # noqa: F401

    with mock.patch("langchain_openai.ChatOpenAI._generate", side_effect=Exception("Mocked Error")):
        with pytest.raises(Exception) as exc_info:
            chat = langchain_openai.ChatOpenAI(temperature=0)
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
            chain.invoke("I love programming.")
        assert str(exc_info.value) == "Mocked Error"
    traces = mock_tracer.pop_traces()
    chain_span = traces[0][0]
    assert chain_span.get_tag("langchain.request.inputs.text") == "I love programming."
    assert chain_span.get_tag("langchain.request.type") == "chain"
    assert chain_span.get_tag("langchain.request.prompt") is None


@pytest.mark.snapshot
def test_pinecone_vectorstore_similarity_search(langchain_community, langchain_openai, request_vcr):
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


@pytest.mark.snapshot
def test_pinecone_vectorstore_retrieval_chain(langchain_community, langchain_openai, request_vcr):
    """
    Test that calling a similarity search on a Pinecone vectorstore with langchain will
    result in a 2-span trace with a vectorstore span and underlying OpenAI embedding interface span.
    """
    import langchain_pinecone
    import pinecone

    with mock.patch("langchain_openai.OpenAIEmbeddings._get_len_safe_embeddings", return_value=[0.0] * 1536):
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
            qa_with_sources.invoke("Who was Alan Turing?")


def test_vectorstore_similarity_search_metrics(
    langchain_community, langchain_openai, request_vcr, mock_metrics, mock_logs, snapshot_tracer
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
    expected_tags = [
        "version:",
        "env:",
        "service:",
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
def test_vectorstore_logs(
    langchain_openai, langchain_community, ddtrace_config_langchain, request_vcr, mock_logs, mock_metrics, mock_tracer
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
                    "message": "sampled langchain_pinecone.vectorstores.PineconeVectorStore",
                    "hostname": mock.ANY,
                    "ddsource": "langchain",
                    "service": "",
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
from langchain_openai import OpenAI
import ddtrace
from tests.contrib.langchain.test_langchain_community import get_request_vcr
llm = OpenAI()
with get_request_vcr(subdirectory_name="langchain_community").use_cassette("openai_completion_sync.yaml"):
    llm.invoke("Can you explain what Descartes meant by 'I think, therefore I am'?")
""",
        env=env,
    )
    assert status == 0, err
    assert out == b""
    assert err == b""


@pytest.mark.snapshot
@pytest.mark.parametrize("schema_version", [None, "v0", "v1"])
@pytest.mark.parametrize("service_name", [None, "mysvc"])
def test_openai_service_name(
    langchain, request_vcr, ddtrace_run_python_code_in_subprocess, schema_version, service_name
):
    import os

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
from langchain_openai import OpenAI
import ddtrace
from tests.contrib.langchain.test_langchain_community import get_request_vcr
llm = OpenAI()
with get_request_vcr(subdirectory_name="langchain_community").use_cassette("openai_completion_sync.yaml"):
    llm.invoke("Can you explain what Descartes meant by 'I think, therefore I am'?")
""",
        env=env,
    )
    assert status == 0, err
    assert out == b""
    assert err == b""


@flaky(1735812000)
@pytest.mark.parametrize(
    "ddtrace_config_langchain",
    [dict(metrics_enabled=False, logs_enabled=True, log_prompt_completion_sample_rate=1.0)],
)
def test_llm_logs_when_response_not_completed(
    langchain, langchain_openai, langchain_community, ddtrace_config_langchain, mock_logs, mock_metrics, mock_tracer
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
                    "service": "",
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
    langchain, langchain_openai, ddtrace_config_langchain, mock_logs, mock_metrics, mock_tracer
):
    """Test that errors get logged even if the response is not returned."""
    with mock.patch("langchain_openai.ChatOpenAI._generate", side_effect=Exception("Mocked Error")):
        with pytest.raises(Exception) as exc_info:
            chat = langchain_openai.ChatOpenAI(temperature=0, max_tokens=256)
            chat.invoke(input=[langchain.schema.HumanMessage(content="Can you please not return an error?")])
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
            "service": "",
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
                    "service": "",
                    "status": "error",
                    "ddtags": "env:,version:,langchain.request.provider:openai,langchain.request.model:text-embedding-ada-002,langchain.request.type:embedding,langchain.request.api_key:...key>",  # noqa: E501
                    "dd.trace_id": hex(trace_id)[2:],
                    "dd.span_id": str(span_id),
                    "inputs": ["Can you please not return an error?"],
                }
            ),
        ]
    )


@flaky(1735812000)
@pytest.mark.snapshot
def test_lcel_chain_simple(langchain_core, langchain_openai, request_vcr):
    prompt = langchain_core.prompts.ChatPromptTemplate.from_messages(
        [("system", "You are world class technical documentation writer."), ("user", "{input}")]
    )
    llm = langchain_openai.OpenAI()

    chain = prompt | llm
    with request_vcr.use_cassette("lcel_openai_chain_call.yaml"):
        chain.invoke({"input": "how can langsmith help with testing?"})


@flaky(1735812000)
@pytest.mark.snapshot
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
@pytest.mark.snapshot
async def test_lcel_chain_simple_async(langchain_core, langchain_openai, request_vcr):
    prompt = langchain_core.prompts.ChatPromptTemplate.from_messages(
        [("system", "You are world class technical documentation writer."), ("user", "{input}")]
    )
    llm = langchain_openai.OpenAI()

    chain = prompt | llm
    with request_vcr.use_cassette("lcel_openai_chain_acall.yaml"):
        await chain.ainvoke({"input": "how can langsmith help with testing?"})


@flaky(1735812000)
@pytest.mark.snapshot
@pytest.mark.skipif(sys.version_info >= (3, 11, 0), reason="Python <3.11 test")
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


@flaky(1735812000)
@pytest.mark.snapshot
@pytest.mark.skipif(sys.version_info < (3, 11, 0), reason="Python 3.11+ required")
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


@flaky(1735812000)
@pytest.mark.snapshot
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


@flaky(1735812000)
@pytest.mark.asyncio
@pytest.mark.snapshot
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


@pytest.mark.parametrize(
    "ddtrace_global_config",
    [dict(_llmobs_enabled=True, _llmobs_sample_rate=1.0, _llmobs_ml_app="langchain_community_test")],
)
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
        LLMObs.disable()
        LLMObs.enable(_tracer=mock_tracer, integrations_enabled=False)  # only want langchain patched

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
        # disable the service before re-enabling it, as it was enabled in another test
        LLMObs.disable()
        LLMObs.enable(_tracer=mock_tracer, integrations_enabled=False)  # only want langchain patched

        with request_vcr.use_cassette(cassette_name):
            generate_trace("Can you explain what an LLM chain is?")
        trace = mock_tracer.pop_traces()[0]

        expected_llmobs_writer_calls = cls._expected_llmobs_chain_calls(
            trace=trace, expected_spans_data=expected_spans_data
        )
        assert mock_llmobs_span_writer.enqueue.call_count == len(expected_spans_data)
        mock_llmobs_span_writer.assert_has_calls(expected_llmobs_writer_calls)

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

    def setUp(self):
        patcher = mock.patch("ddtrace.llmobs._llmobs.LLMObsSpanWriter")
        LLMObsSpanWriterMock = patcher.start()
        mock_llmobs_span_writer = mock.MagicMock()
        LLMObsSpanWriterMock.return_value = mock_llmobs_span_writer

        self.mock_llmobs_span_writer = mock_llmobs_span_writer

        super(TestLangchainTraceStructureWithLlmIntegrations, self).setUp()

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

    def _call_bedrock_chat_model(self, ChatBedrock, HumanMessage):
        chat = ChatBedrock(
            model_id="amazon.titan-tg1-large",
            model_kwargs={"max_tokens": 50, "temperature": 0},
        )
        messages = [HumanMessage(content="summarize the plot to the lord of the rings in a dozen words")]
        with get_request_vcr(subdirectory_name="langchain_community").use_cassette("bedrock_amazon_chat_invoke.yaml"):
            chat.invoke(messages)

    def _call_bedrock_llm(self, Bedrock, ConversationChain, ConversationBufferMemory):
        llm = Bedrock(
            model_id="amazon.titan-tg1-large",
            region_name="us-east-1",
            model_kwargs={"temperature": 0, "topP": 0.9, "stopSequences": [], "maxTokens": 50},
        )

        conversation = ConversationChain(llm=llm, verbose=True, memory=ConversationBufferMemory())

        with get_request_vcr(subdirectory_name="langchain_community").use_cassette("bedrock_amazon_invoke.yaml"):
            conversation.predict(input="can you explain what Datadog is to someone not in the tech industry?")

    def _call_openai_llm(self, OpenAI):
        llm = OpenAI()
        with get_request_vcr(subdirectory_name="langchain_community").use_cassette("openai_completion_sync.yaml"):
            llm.invoke("Can you explain what Descartes meant by 'I think, therefore I am'?")

    @run_in_subprocess(env_overrides=bedrock_env_config)
    def test_llmobs_with_chat_model_bedrock_enabled(self):
        from langchain_aws import ChatBedrock
        from langchain_core.messages import HumanMessage

        from ddtrace import patch
        from ddtrace.llmobs import LLMObs

        patch(langchain=True, botocore=True)
        LLMObs.enable(ml_app="<ml-app-name>", integrations_enabled=False, agentless_enabled=True)

        self._call_bedrock_chat_model(ChatBedrock, HumanMessage)

        self._assert_trace_structure_from_writer_call_args(["workflow", "llm"])

        LLMObs.disable()

    @run_in_subprocess(env_overrides=bedrock_env_config)
    def test_llmobs_with_chat_model_bedrock_disabled(self):
        from langchain_aws import ChatBedrock
        from langchain_core.messages import HumanMessage

        from ddtrace import patch
        from ddtrace.llmobs import LLMObs

        patch(langchain=True)
        LLMObs.enable(ml_app="<ml-app-name>", integrations_enabled=False, agentless_enabled=True)

        self._call_bedrock_chat_model(ChatBedrock, HumanMessage)

        self._assert_trace_structure_from_writer_call_args(["llm"])

        LLMObs.disable()

    @run_in_subprocess(env_overrides=bedrock_env_config)
    def test_llmobs_with_llm_model_bedrock_enabled(self):
        from langchain.chains import ConversationChain
        from langchain.memory import ConversationBufferMemory
        from langchain_community.llms import Bedrock

        from ddtrace import patch
        from ddtrace.llmobs import LLMObs

        patch(langchain=True, botocore=True)
        LLMObs.enable(ml_app="<ml-app-name>", integrations_enabled=False, agentless_enabled=True)
        self._call_bedrock_llm(Bedrock, ConversationChain, ConversationBufferMemory)
        self._assert_trace_structure_from_writer_call_args(["workflow", "workflow", "llm"])

        LLMObs.disable()

    @run_in_subprocess(env_overrides=bedrock_env_config)
    def test_llmobs_with_llm_model_bedrock_disabled(self):
        from langchain.chains import ConversationChain
        from langchain.memory import ConversationBufferMemory
        from langchain_community.llms import Bedrock

        from ddtrace import patch
        from ddtrace.llmobs import LLMObs

        patch(langchain=True)
        LLMObs.enable(ml_app="<ml-app-name>", integrations_enabled=False, agentless_enabled=True)
        self._call_bedrock_llm(Bedrock, ConversationChain, ConversationBufferMemory)
        self._assert_trace_structure_from_writer_call_args(["workflow", "llm"])

        LLMObs.disable()

    @run_in_subprocess(env_overrides=openai_env_config)
    def test_llmobs_langchain_with_openai_enabled(self):
        from langchain_openai import OpenAI

        from ddtrace import patch
        from ddtrace.llmobs import LLMObs

        patch(langchain=True, openai=True)
        LLMObs.enable(ml_app="<ml-app-name>", integrations_enabled=False, agentless_enabled=True)
        self._call_openai_llm(OpenAI)
        self._assert_trace_structure_from_writer_call_args(["workflow", "llm"])

        LLMObs.disable()

    @run_in_subprocess(env_overrides=openai_env_config)
    def test_llmobs_langchain_with_openai_disabled(self):
        from langchain_openai import OpenAI

        from ddtrace import patch
        from ddtrace.llmobs import LLMObs

        patch(langchain=True)

        LLMObs.enable(ml_app="<ml-app-name>", integrations_enabled=False, agentless_enabled=True)
        self._call_openai_llm(OpenAI)
        self._assert_trace_structure_from_writer_call_args(["llm"])

        LLMObs.disable()
