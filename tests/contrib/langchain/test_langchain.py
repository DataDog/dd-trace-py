import os
import re

from langchain import PromptTemplate
from langchain.chains import LLMChain
from langchain.chains import LLMMathChain
from langchain.chains import RetrievalQAWithSourcesChain
from langchain.chains import SequentialChain
from langchain.chains import TransformChain
from langchain.chat_models import ChatOpenAI
from langchain.embeddings import OpenAIEmbeddings
from langchain.llms import OpenAI
from langchain.schema import HumanMessage
from langchain.schema import SystemMessage
from langchain.vectorstores import Pinecone
import pytest
import vcr

from ddtrace.contrib.langchain.patch import patch
from ddtrace.contrib.langchain.patch import unpatch


@pytest.fixture(autouse=True)
def patch_langchain(request):
    if "integrationTest" in request.keywords:
        yield
        return
    os.environ["OPENAI_API_KEY"] = "<not-a-real-key>"
    patch()
    yield
    unpatch()


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


@pytest.mark.snapshot
def test_openai_llm_sync(request_vcr):
    llm = OpenAI()
    with request_vcr.use_cassette("openai_completion_sync.yaml"):
        llm("Can you explain what Descartes meant by 'I think, therefore I am'?")


@pytest.mark.snapshot
def test_openai_llm_sync_multiple_prompts(request_vcr):
    llm = OpenAI()
    with request_vcr.use_cassette("openai_completion_sync_multi_prompt.yaml"):
        llm.generate(
            [
                "What is the best way to teach a baby multiple languages?",
                "How many times has Spongebob failed his road test?",
            ]
        )


@pytest.mark.asyncio
@pytest.mark.snapshot
async def test_openai_llm_async(request_vcr):
    llm = OpenAI()
    with request_vcr.use_cassette("openai_completion_async.yaml"):
        await llm.agenerate(["Which team won the 2019 NBA finals?"])


@pytest.mark.snapshot(token="tests.contrib.langchain.test_langchain.test_openai_llm_stream")
def test_openai_llm_sync_stream(request_vcr):
    llm = OpenAI(streaming=True)
    with request_vcr.use_cassette("openai_completion_sync_stream.yaml"):
        llm("Why is Spongebob so bad at driving?")


@pytest.mark.asyncio
@pytest.mark.snapshot(
    token="tests.contrib.langchain.test_langchain.test_openai_llm_stream",
    ignores=["meta.langchain.response.completions.0.text"],
)
async def test_openai_llm_async_stream(request_vcr):
    llm = OpenAI(streaming=True)
    with request_vcr.use_cassette("openai_completion_async_stream.yaml"):
        await llm.agenerate(["Why is Spongebob so bad at driving?"])


@pytest.mark.snapshot(ignores=["meta.error.stack"])
def test_openai_llm_error(request_vcr):
    llm = OpenAI()
    with pytest.raises(Exception):
        with request_vcr.use_cassette("openai_completion_error.yaml"):
            llm.generate([12345, 123456])


@pytest.mark.snapshot(token="tests.contrib.langchain.test_langchain.test_openai_chat_model_call")
def test_openai_chat_model_sync_call(request_vcr):
    chat = ChatOpenAI(temperature=0, max_tokens=256)
    with request_vcr.use_cassette("openai_chat_completion_sync_call.yaml"):
        chat([HumanMessage(content="When do you use 'whom' instead of 'who'?")])


@pytest.mark.snapshot(token="tests.contrib.langchain.test_langchain.test_openai_chat_model_generate")
def test_openai_chat_model_sync_generate(request_vcr):
    chat = ChatOpenAI(temperature=0, max_tokens=256)
    with request_vcr.use_cassette("openai_chat_completion_sync_generate.yaml"):
        chat.generate(
            [
                [
                    SystemMessage(content="Respond like a frat boy."),
                    HumanMessage(content="Where's the nearest equinox gym from Hudson Yards manhattan?"),
                ],
                [
                    SystemMessage(content="Respond with a pirate accent."),
                    HumanMessage(content="How does one get to Bikini Bottom from New York?"),
                ],
            ]
        )


@pytest.mark.asyncio
@pytest.mark.snapshot(token="tests.contrib.langchain.test_langchain.test_openai_chat_model_call")
async def test_openai_chat_model_async_call(request_vcr):
    chat = ChatOpenAI(temperature=0, max_tokens=256)
    with request_vcr.use_cassette("openai_chat_completion_async_call.yaml"):
        await chat._call_async([HumanMessage(content="When do you use 'whom' instead of 'who'?")])


@pytest.mark.asyncio
@pytest.mark.snapshot(token="tests.contrib.langchain.test_langchain.test_openai_chat_model_generate")
async def test_openai_chat_model_async_generate(request_vcr):
    chat = ChatOpenAI(temperature=0, max_tokens=256)
    with request_vcr.use_cassette("openai_chat_completion_async_generate.yaml"):
        await chat.agenerate(
            [
                [
                    SystemMessage(content="Respond like a frat boy."),
                    HumanMessage(content="Where's the nearest equinox gym from Hudson Yards manhattan?"),
                ],
                [
                    SystemMessage(content="Respond with a pirate accent."),
                    HumanMessage(content="How does one get to Bikini Bottom from New York?"),
                ],
            ]
        )


@pytest.mark.snapshot(token="tests.contrib.langchain.test_langchain.test_openai_chat_model_stream")
def test_openai_chat_model_sync_stream(request_vcr):
    chat = ChatOpenAI(streaming=True, temperature=0, max_tokens=256)
    with request_vcr.use_cassette("openai_chat_completion_sync_stream.yaml"):
        chat([HumanMessage(content="What is the secret Krabby Patty recipe?")])


@pytest.mark.asyncio
@pytest.mark.snapshot(token="tests.contrib.langchain.test_langchain.test_openai_chat_model_stream")
async def test_openai_chat_model_async_stream(request_vcr):
    chat = ChatOpenAI(streaming=True, temperature=0, max_tokens=256)
    with request_vcr.use_cassette("openai_chat_completion_async_stream.yaml"):
        await chat.agenerate([[HumanMessage(content="What is the secret Krabby Patty recipe?")]])


@pytest.mark.snapshot
def test_openai_embedding_query(request_vcr):
    embeddings = OpenAIEmbeddings()
    with request_vcr.use_cassette("openai_embedding_query.yaml"):
        embeddings.embed_query("this is a test query.")


@pytest.mark.snapshot
def test_openai_embedding_document(request_vcr):
    embeddings = OpenAIEmbeddings()
    with request_vcr.use_cassette("openai_embedding_document.yaml"):
        embeddings.embed_documents(["this is", "a test document."])


@pytest.mark.snapshot(token="tests.contrib.langchain.test_langchain.test_openai_math_chain")
def test_openai_math_chain_sync(request_vcr):
    """
    Test that using the provided LLMMathChain will result in a 3-span trace with
    the overall LLMMathChain, LLMChain, and underlying OpenAI interface.
    """
    chain = LLMMathChain(llm=OpenAI(temperature=0))
    with request_vcr.use_cassette("openai_math_chain_sync.yaml"):
        chain.run("what is two raised to the fifty-fourth power?")


@pytest.mark.asyncio
@pytest.mark.snapshot(token="tests.contrib.langchain.test_langchain.test_openai_math_chain")
async def test_openai_math_chain_async(request_vcr):
    """
    Test that using the provided LLMMathChain will result in a 3-span trace with
    the overall LLMMathChain, LLMChain, and underlying OpenAI interface.
    """
    chain = LLMMathChain(llm=OpenAI(temperature=0))
    with request_vcr.use_cassette("openai_math_chain_async.yaml"):
        await chain.acall("what is two raised to the fifty-fourth power?")


@pytest.mark.snapshot(token="tests.contrib.langchain.test_langchain.test_openai_sequential_chain")
def test_openai_sequential_chain(request_vcr):
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

    clean_extra_spaces_chain = TransformChain(
        input_variables=["text"], output_variables=["output_text"], transform=_transform_func
    )
    template = """Paraphrase this text:

        {output_text}

        In the style of a {style}.

        Paraphrase: """
    prompt = PromptTemplate(input_variables=["style", "output_text"], template=template)
    style_paraphrase_chain = LLMChain(llm=OpenAI(), prompt=prompt, output_key="final_output")
    sequential_chain = SequentialChain(
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
def test_openai_sequential_chain_with_multiple_llm(request_vcr):
    template = """Paraphrase this text:

        {input_text}

        Paraphrase: """
    prompt = PromptTemplate(input_variables=["input_text"], template=template)
    style_paraphrase_chain = LLMChain(llm=OpenAI(), prompt=prompt, output_key="paraphrased_output")
    rhyme_template = """Make this text rhyme:

        {paraphrased_output}

        Rhyme: """
    rhyme_prompt = PromptTemplate(input_variables=["paraphrased_output"], template=rhyme_template)
    rhyme_chain = LLMChain(llm=OpenAI(), prompt=rhyme_prompt, output_key="final_output")
    sequential_chain = SequentialChain(
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
    with request_vcr.use_cassette("openai_sequential_paraphrase_and_rhyme.yaml"):
        sequential_chain.run({"input_text": input_text})


@pytest.mark.snapshot
def test_pinecone_vectorstore_similarity_search(request_vcr):
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
        embed = OpenAIEmbeddings(
            model="text-embedding-ada-002", openai_api_key=os.getenv("OPENAI_API_KEY", "<not-a-real-key>")
        )
        index = pinecone.Index(index_name="langchain-retrieval")
        vectorstore = Pinecone(index, embed.embed_query, "text")
        vectorstore.similarity_search("Who was Alan Turing?", 1)


@pytest.mark.snapshot
def test_pinecone_vectorstore_retrieval_chain(request_vcr):
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
        embed = OpenAIEmbeddings(
            model="text-embedding-ada-002", openai_api_key=os.getenv("OPENAI_API_KEY", "<not-a-real-key>")
        )
        index = pinecone.Index(index_name="langchain-retrieval")
        vectorstore = Pinecone(index, embed.embed_query, "text")

        llm = OpenAI()
        qa_with_sources = RetrievalQAWithSourcesChain.from_chain_type(
            llm=llm, chain_type="stuff", retriever=vectorstore.as_retriever()
        )
        qa_with_sources("Who was Alan Turing?")


@pytest.mark.integrationTest
@pytest.mark.snapshot
def test_openai_integration(request_vcr, ddtrace_run_python_code_in_subprocess):
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
