import os
import re

import mock
import pytest
import vcr

from ddtrace import Pin
from ddtrace.contrib.langchain.patch import patch
from ddtrace.contrib.langchain.patch import unpatch
from tests.utils import DummyTracer
from tests.utils import DummyWriter
from tests.utils import override_config
from tests.utils import override_global_config


# VCR is used to capture and store network requests made to OpenAI and other APIs.
# This is done to avoid making real calls to the API which could introduce
# flakiness and cost.

# To (re)-generate the cassettes: pass a real API key with
# {PROVIDER}_API_KEY, delete the old cassettes and re-run the tests.
# NOTE: be sure to check that the generated cassettes don't contain your
#       API key. Keys should be redacted by the filter_headers option below.
# NOTE: that different cassettes have to be used between sync and async
#       due to this issue: https://github.com/kevin1024/vcrpy/issues/463
#       between cassettes generated for requests and aiohttp.
def get_request_vcr():
    return vcr.VCR(
        cassette_library_dir=os.path.join(os.path.dirname(__file__), "cassettes/"),
        record_mode="once",
        match_on=["path"],
        filter_headers=["authorization", "OpenAI-Organization", "api-key"],
        # Ignore requests to the agent
        ignore_localhost=True,
    )


@pytest.fixture(scope="session")
def request_vcr():
    yield get_request_vcr()


@pytest.fixture
def ddtrace_config_langchain():
    return {}


@pytest.fixture
def langchain(ddtrace_config_langchain, mock_logs, mock_metrics):
    with override_config("langchain", ddtrace_config_langchain):
        os.environ["OPENAI_API_KEY"] = "<not-a-real-key>"
        patch()
        import langchain

        yield langchain
        unpatch()


@pytest.fixture(scope="session")
def mock_metrics():
    patcher = mock.patch("ddtrace.contrib._trace_utils_llm.get_dogstatsd_client")
    DogStatsdMock = patcher.start()
    m = mock.MagicMock()
    DogStatsdMock.return_value = m
    yield m
    patcher.stop()


@pytest.fixture
def mock_logs(scope="session"):
    """
    Note that this fixture must be ordered BEFORE mock_tracer as it needs to patch the log writer
    before it is instantiated.
    """
    patcher = mock.patch("ddtrace.contrib._trace_utils_llm.V2LogWriter")
    V2LogWriterMock = patcher.start()
    m = mock.MagicMock()
    V2LogWriterMock.return_value = m
    yield m
    patcher.stop()


@pytest.fixture
def mock_tracer(langchain, mock_logs, mock_metrics):
    pin = Pin.get_from(langchain)
    mock_tracer = DummyTracer(writer=DummyWriter(trace_flush_enabled=False))
    pin.override(langchain, tracer=mock_tracer)
    pin.tracer.configure()
    yield mock_tracer

    mock_logs.reset_mock()
    mock_metrics.reset_mock()


@pytest.mark.xfail(reason="An API key is required when logs are enabled")
@pytest.mark.parametrize("ddtrace_config_langchain", [dict(_api_key="", logs_enabled=True)])
def test_logs_no_api_key(langchain, ddtrace_config_langchain, mock_tracer):
    """When no DD_API_KEY is set, the patching fails"""
    pass


@pytest.mark.parametrize("ddtrace_config_langchain", [dict(metrics_enabled=b) for b in [True, False]])
def test_enable_metrics(langchain, ddtrace_config_langchain, request_vcr, mock_metrics, mock_logs, mock_tracer):
    """Ensure the metrics_enabled configuration works."""
    llm = langchain.llms.OpenAI()
    with override_global_config(dict(service="test-svc", env="staging", version="1234")):
        with request_vcr.use_cassette("openai_completion_sync.yaml"):
            llm("What does Nietzsche mean by 'God is dead'?")
    if ddtrace_config_langchain["metrics_enabled"]:
        assert mock_metrics.mock_calls
    else:
        assert not mock_metrics.mock_calls


@pytest.mark.parametrize(
    "ddtrace_config_langchain",
    [dict(_api_key="<not-real-but-it's-something>", logs_enabled=True, log_prompt_completion_sample_rate=1.0)],
)
def test_global_tags(langchain, ddtrace_config_langchain, request_vcr, mock_metrics, mock_logs, mock_tracer):
    """
    When the global config UST tags are set
        The service name should be used for all data
        The env should be used for all data
        The version should be used for all data
    """
    llm = langchain.llms.OpenAI()
    with override_global_config(dict(service="test-svc", env="staging", version="1234")):
        with request_vcr.use_cassette("openai_completion_sync.yaml"):
            llm("What does Nietzsche mean by 'God is dead'?")

    span = mock_tracer.pop_traces()[0][0]
    assert span.resource == "langchain.llms.openai.OpenAI"
    assert span.service == "test-svc"
    assert span.get_tag("env") == "staging"
    assert span.get_tag("version") == "1234"
    assert span.get_tag("langchain.request.provider") == "openai"
    assert span.get_tag("langchain.request.model") == "text-davinci-003"
    assert span.get_tag("langchain.request.openai.api_key") == "...key>"

    assert mock_logs.enqueue.call_count == 1
    assert mock_metrics.mock_calls
    for _, args, kwargs in mock_metrics.mock_calls:
        expected_metrics = [
            "service:test-svc",
            "env:staging",
            "version:1234",
            "langchain.request.model:text-davinci-003",
            "langchain.request.provider:openai",
            "langchain.request.openai.api_key:...key>",
        ]
        actual_tags = kwargs.get("tags")
        for m in expected_metrics:
            assert m in actual_tags

    for call, args, kwargs in mock_logs.mock_calls:
        if call != "enqueue":
            continue
        log = args[0]
        assert log["service"] == "test-svc"
        assert (
            log["ddtags"]
            == "env:staging,version:1234,langchain.request.provider:openai,langchain.request.model:text-davinci-003,langchain.request.openai.api_key:...key>"  # noqa: E501
        )


@pytest.mark.snapshot
def test_openai_llm_sync(langchain, request_vcr):
    llm = langchain.llms.OpenAI()
    with request_vcr.use_cassette("openai_completion_sync.yaml"):
        llm("Can you explain what Descartes meant by 'I think, therefore I am'?")


@pytest.mark.snapshot
def test_openai_llm_sync_multiple_prompts(langchain, request_vcr):
    llm = langchain.llms.OpenAI()
    with request_vcr.use_cassette("openai_completion_sync_multi_prompt.yaml"):
        llm.generate(
            [
                "What is the best way to teach a baby multiple languages?",
                "How many times has Spongebob failed his road test?",
            ]
        )


@pytest.mark.asyncio
@pytest.mark.snapshot
async def test_openai_llm_async(langchain, request_vcr):
    llm = langchain.llms.OpenAI()
    with request_vcr.use_cassette("openai_completion_async.yaml"):
        await llm.agenerate(["Which team won the 2019 NBA finals?"])


@pytest.mark.snapshot(token="tests.contrib.langchain.test_langchain.test_openai_llm_stream")
def test_openai_llm_sync_stream(langchain, request_vcr):
    llm = langchain.llms.OpenAI(streaming=True)
    with request_vcr.use_cassette("openai_completion_sync_stream.yaml"):
        llm("Why is Spongebob so bad at driving?")


@pytest.mark.asyncio
@pytest.mark.snapshot(
    token="tests.contrib.langchain.test_langchain.test_openai_llm_stream",
    ignores=["meta.langchain.response.completions.0.text"],
)
async def test_openai_llm_async_stream(langchain, request_vcr):
    llm = langchain.llms.OpenAI(streaming=True)
    with request_vcr.use_cassette("openai_completion_async_stream.yaml"):
        await llm.agenerate(["Why is Spongebob so bad at driving?"])


@pytest.mark.snapshot(ignores=["meta.error.stack"])
def test_openai_llm_error(langchain, request_vcr):
    llm = langchain.llms.OpenAI()
    with pytest.raises(Exception):
        with request_vcr.use_cassette("openai_completion_error.yaml"):
            llm.generate([12345, 123456])


@pytest.mark.snapshot
def test_cohere_llm_sync(langchain, request_vcr):
    llm = langchain.llms.Cohere(cohere_api_key=os.getenv("COHERE_API_KEY", "<not-a-real-key>"))
    with request_vcr.use_cassette("cohere_completion_sync.yaml"):
        llm("What is the secret Krabby Patty recipe?")


@pytest.mark.snapshot
def test_huggingfacehub_llm_sync(langchain, request_vcr):
    llm = langchain.llms.HuggingFaceHub(
        repo_id="google/flan-t5-xxl",
        model_kwargs={"temperature": 0.5, "max_length": 256},
        huggingfacehub_api_token=os.getenv("HUGGINGFACEHUB_API_TOKEN", "<not-a-real-key>"),
    )
    with request_vcr.use_cassette("huggingfacehub_completion_sync.yaml"):
        llm("Why does Mr. Krabs have a whale daughter?")


@pytest.mark.snapshot
def test_ai21_llm_sync(langchain, request_vcr):
    llm = langchain.llms.AI21(ai21_api_key=os.getenv("AI21_API_KEY", "<not-a-real-key>"))
    with request_vcr.use_cassette("ai21_completion_sync.yaml"):
        llm("Why does everyone in Bikini Bottom hate Plankton?")


@pytest.mark.snapshot(token="tests.contrib.langchain.test_langchain.test_openai_chat_model_call")
def test_openai_chat_model_sync_call(langchain, request_vcr):
    chat = langchain.chat_models.ChatOpenAI(temperature=0, max_tokens=256)
    with request_vcr.use_cassette("openai_chat_completion_sync_call.yaml"):
        chat([langchain.schema.HumanMessage(content="When do you use 'whom' instead of 'who'?")])


@pytest.mark.snapshot(token="tests.contrib.langchain.test_langchain.test_openai_chat_model_generate")
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


@pytest.mark.asyncio
@pytest.mark.snapshot(token="tests.contrib.langchain.test_langchain.test_openai_chat_model_call")
async def test_openai_chat_model_async_call(langchain, request_vcr):
    chat = langchain.chat_models.ChatOpenAI(temperature=0, max_tokens=256)
    with request_vcr.use_cassette("openai_chat_completion_async_call.yaml"):
        await chat._call_async([langchain.schema.HumanMessage(content="When do you use 'whom' instead of 'who'?")])


@pytest.mark.asyncio
@pytest.mark.snapshot(token="tests.contrib.langchain.test_langchain.test_openai_chat_model_generate")
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


@pytest.mark.snapshot(token="tests.contrib.langchain.test_langchain.test_openai_chat_model_stream")
def test_openai_chat_model_sync_stream(langchain, request_vcr):
    chat = langchain.chat_models.ChatOpenAI(streaming=True, temperature=0, max_tokens=256)
    with request_vcr.use_cassette("openai_chat_completion_sync_stream.yaml"):
        chat([langchain.schema.HumanMessage(content="What is the secret Krabby Patty recipe?")])


@pytest.mark.asyncio
@pytest.mark.snapshot(token="tests.contrib.langchain.test_langchain.test_openai_chat_model_stream")
async def test_openai_chat_model_async_stream(langchain, request_vcr):
    chat = langchain.chat_models.ChatOpenAI(streaming=True, temperature=0, max_tokens=256)
    with request_vcr.use_cassette("openai_chat_completion_async_stream.yaml"):
        await chat.agenerate([[langchain.schema.HumanMessage(content="What is the secret Krabby Patty recipe?")]])


@pytest.mark.snapshot
def test_openai_embedding_query(langchain, request_vcr):
    embeddings = langchain.embeddings.OpenAIEmbeddings()
    with request_vcr.use_cassette("openai_embedding_query.yaml"):
        embeddings.embed_query("this is a test query.")


@pytest.mark.snapshot
def test_openai_embedding_document(langchain, request_vcr):
    embeddings = langchain.embeddings.OpenAIEmbeddings()
    with request_vcr.use_cassette("openai_embedding_document.yaml"):
        embeddings.embed_documents(["this is", "a test document."])


@pytest.mark.snapshot
def test_fake_embedding_query(langchain):
    embeddings = langchain.embeddings.FakeEmbeddings(size=99)
    embeddings.embed_query("foo")


@pytest.mark.snapshot
def test_fake_embedding_document(langchain):
    embeddings = langchain.embeddings.FakeEmbeddings(size=99)
    embeddings.embed_documents(["foo", "bar"])


@pytest.mark.snapshot(token="tests.contrib.langchain.test_langchain.test_openai_math_chain")
def test_openai_math_chain_sync(langchain, request_vcr):
    """
    Test that using the provided LLMMathChain will result in a 3-span trace with
    the overall LLMMathChain, LLMChain, and underlying OpenAI interface.
    """
    chain = langchain.chains.LLMMathChain(llm=langchain.llms.OpenAI(temperature=0))
    with request_vcr.use_cassette("openai_math_chain_sync.yaml"):
        chain.run("what is two raised to the fifty-fourth power?")


@pytest.mark.asyncio
@pytest.mark.snapshot(token="tests.contrib.langchain.test_langchain.test_openai_math_chain")
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


@pytest.mark.snapshot(token="tests.contrib.langchain.test_langchain.test_openai_sequential_chain")
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


@pytest.mark.snapshot
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
        sequential_chain.run({"input_text": input_text})


@pytest.mark.asyncio
@pytest.mark.snapshot
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
        await sequential_chain.acall({"input_text": input_text})


@pytest.mark.snapshot
def test_pinecone_vectorstore_similarity_search(langchain, request_vcr):
    """
    Test that calling a similarity search on a Pinecone vectorstore with langchain will
    result in a 2-span trace with a vectorstore span and underlying OpenAI embedding interface span.
    """
    import pinecone

    with request_vcr.use_cassette("openai_pinecone_similarity_search.yaml"):
        pinecone.init(
            api_key=os.getenv("PINECONE_API_KEY", "<not-a-real-key>"),
            environment=os.getenv("PINECONE_ENV", "<not-a-real-env>"),
        )
        embed = langchain.embeddings.OpenAIEmbeddings(
            model="text-embedding-ada-002", openai_api_key=os.getenv("OPENAI_API_KEY", "<not-a-real-key>")
        )
        index = pinecone.Index(index_name="langchain-retrieval")
        vectorstore = langchain.vectorstores.Pinecone(index, embed.embed_query, "text")
        vectorstore.similarity_search("Who was Alan Turing?", 1)


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
        embed = langchain.embeddings.OpenAIEmbeddings(
            model="text-embedding-ada-002", openai_api_key=os.getenv("OPENAI_API_KEY", "<not-a-real-key>")
        )
        index = pinecone.Index(index_name="langchain-retrieval")
        vectorstore = langchain.vectorstores.Pinecone(index, embed.embed_query, "text")

        llm = langchain.llms.OpenAI()
        qa_with_sources = langchain.chains.RetrievalQAWithSourcesChain.from_chain_type(
            llm=llm, chain_type="stuff", retriever=vectorstore.as_retriever()
        )
        qa_with_sources("Who was Alan Turing?")


@pytest.mark.integrationTest
@pytest.mark.snapshot
def test_openai_integration(langchain, request_vcr, ddtrace_run_python_code_in_subprocess):
    env = os.environ.copy()
    pypath = [os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))]
    if "PYTHONPATH" in env:
        pypath.append(env["PYTHONPATH"])
    env.update(
        {
            "PYTHONPATH": ":".join(pypath),
            # Disable metrics because the test agent doesn't support metrics
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
with get_request_vcr().use_cassette("openai_completion_sync.yaml"):
    llm("Can you explain what Descartes meant by 'I think, therefore I am'?")
""",
        env=env,
    )
    assert status == 0, err
    assert out == b""
    assert err == b""
