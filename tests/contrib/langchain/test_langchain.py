from operator import itemgetter
import os
import sys

import langchain
import langchain.prompts  # noqa: F401
import mock
import pytest

from ddtrace.internal.utils.version import parse_version
from tests.contrib.langchain.utils import get_request_vcr


LANGCHAIN_VERSION = parse_version(langchain.__version__)

IGNORE_FIELDS = [
    "resources",
    "meta.openai.request.logprobs",  # langchain-openai llm call now includes logprobs as param
    "meta.error.stack",
    "meta.http.useragent",
    "meta.langchain.request.openai-chat.parameters.logprobs",
    "meta.langchain.request.openai.parameters.logprobs",
    "meta.langchain.request.openai.parameters.seed",  # langchain-openai llm call now includes seed as param
    "meta.langchain.request.openai.parameters.logprobs",  # langchain-openai llm call now includes seed as param
    # these are sometimes named differently
    "meta.langchain.request.openai.parameters.max_tokens",
    "meta.langchain.request.openai.parameters.max_completion_tokens",
    "meta.langchain.request.openai-chat.parameters.max_completion_tokens"
    "meta.langchain.request.openai-chat.parameters.max_tokens",
    "meta.langchain.request.api_key",
]


@pytest.fixture(scope="session")
def request_vcr():
    yield get_request_vcr()


@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_openai_llm_sync(langchain_openai, openai_completion):
    llm = langchain_openai.OpenAI()
    llm.invoke("Can you explain what Descartes meant by 'I think, therefore I am'?")


@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_openai_llm_sync_multiple_prompts(langchain_openai, openai_completion_multiple):
    llm = langchain_openai.OpenAI()
    llm.generate(
        prompts=[
            "What is the best way to teach a baby multiple languages?",
            "How many times has Spongebob failed his road test?",
        ]
    )


@pytest.mark.asyncio
@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
async def test_openai_llm_async(langchain_openai, openai_completion):
    llm = langchain_openai.OpenAI()
    await llm.agenerate(["Which team won the 2019 NBA finals?"])


@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_openai_llm_error(langchain, langchain_openai, openai_completion_error):
    import openai  # Imported here because the os env OPENAI_API_KEY needs to be set via langchain fixture before import

    llm = langchain_openai.OpenAI()

    with pytest.raises(openai.BadRequestError):
        llm.generate([12345, 123456])


@pytest.mark.skipif(not ((0, 2) <= LANGCHAIN_VERSION < (0, 3)), reason="Compatible with langchain==0.2 only")
@pytest.mark.snapshot
def test_cohere_llm_sync_0_2(langchain_cohere, request_vcr):
    llm = langchain_cohere.llms.Cohere(cohere_api_key=os.getenv("COHERE_API_KEY", "<not-a-real-key>"))
    with request_vcr.use_cassette("cohere_completion_sync_0_2.yaml"):
        llm.invoke("What is the secret Krabby Patty recipe?")


@pytest.mark.skip(reason="https://github.com/langchain-ai/langchain-cohere/issues/44")
@pytest.mark.skipif(LANGCHAIN_VERSION < (0, 3), reason="Compatible with langchain>=0.3 only")
@pytest.mark.snapshot
def test_cohere_llm_sync_latest(langchain_cohere, request_vcr):
    with request_vcr.use_cassette("cohere_completion_sync_latest.yaml"):
        llm = langchain_cohere.llms.Cohere(
            cohere_api_key=os.getenv("COHERE_API_KEY", "<not-a-real-key>"),
            max_tokens=256,
            temperature=0.75,
        )
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


@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_openai_chat_model_sync_call_langchain_openai(langchain_openai, openai_chat_completion):
    chat = langchain_openai.ChatOpenAI(temperature=0, max_tokens=256, n=1)
    chat.invoke(input=[langchain.schema.HumanMessage(content="When do you use 'whom' instead of 'who'?")])


@pytest.mark.asyncio
@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
async def test_openai_chat_model_sync_call_langchain_openai_async(langchain_openai, openai_chat_completion):
    chat = langchain_openai.ChatOpenAI(temperature=0, max_tokens=256, n=1)
    await chat.ainvoke(input=[langchain.schema.HumanMessage(content="When do you use 'whom' instead of 'who'?")])


# TODO: come back and clean this one up... seems like we tag 4 responses
@pytest.mark.skipif(LANGCHAIN_VERSION < (0, 3), reason="Requires at least LangChain 0.3")
@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_openai_chat_model_sync_generate(langchain_openai, openai_chat_completion_multiple):
    chat = langchain_openai.ChatOpenAI(temperature=0, max_tokens=256)
    chat.generate(
        [
            [
                langchain.schema.SystemMessage(content="Respond like a frat boy."),
                langchain.schema.HumanMessage(content="Where's the nearest equinox gym from Hudson Yards manhattan?"),
            ],
            [
                langchain.schema.SystemMessage(content="Respond with a pirate accent."),
                langchain.schema.HumanMessage(content="How does one get to Bikini Bottom from New York?"),
            ],
        ]
    )


@pytest.mark.snapshot(
    ignores=IGNORE_FIELDS,
    variants={
        "0_2": LANGCHAIN_VERSION < (0, 3),
        "latest": LANGCHAIN_VERSION >= (0, 3),
    },
)
def test_openai_chat_model_vision_generate(langchain_openai, request_vcr):
    """
    Test that input messages with nested contents are still tagged without error
    Regression test for https://github.com/DataDog/dd-trace-py/issues/8149.
    """
    image_url = (
        "https://upload.wikimedia.org/wikipedia/commons/thumb/d/dd/Gfp-wisconsin-madison-the-nature-boardwalk"
        ".jpg/2560px-Gfp-wisconsin-madison-the-nature-boardwalk.jpg"
    )
    chat = langchain_openai.ChatOpenAI(model="gpt-4o", temperature=0, max_tokens=256)
    with request_vcr.use_cassette("openai_chat_completion_image_input_sync_generate.yaml"):
        chat.generate(
            [
                [
                    langchain.schema.HumanMessage(
                        content=[
                            {"type": "text", "text": "What’s in this image?"},
                            {
                                "type": "image_url",
                                "image_url": {"url": image_url},
                            },
                        ],
                    ),
                ],
            ]
        )


@pytest.mark.skipif(
    LANGCHAIN_VERSION < (0, 3), reason="Bug in langchain: https://github.com/langchain-ai/langgraph/issues/136"
)
@pytest.mark.asyncio
@pytest.mark.snapshot(
    ignores=IGNORE_FIELDS
    + [
        # async batch requests can result in a non-deterministic order
        "meta.langchain.response.completions.0.0.content",
        "meta.langchain.response.completions.1.0.content",
    ]
)
async def test_openai_chat_model_async_generate(langchain_openai, request_vcr):
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


@pytest.mark.snapshot
def test_openai_embedding_query(langchain_openai, openai_embedding):
    with mock.patch("langchain_openai.OpenAIEmbeddings._get_len_safe_embeddings", return_value=[0.0] * 1536):
        embeddings = langchain_openai.OpenAIEmbeddings()
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


@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_lcel_chain_simple(langchain_core, langchain_openai, openai_completion):
    prompt = langchain_core.prompts.ChatPromptTemplate.from_messages(
        [("system", "You are world class technical documentation writer."), ("user", "{input}")]
    )
    llm = langchain_openai.OpenAI()

    chain = prompt | llm
    chain.invoke({"input": "how can langsmith help with testing?"})


@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_lcel_chain_complicated(langchain_core, langchain_openai, openai_chat_completion):
    prompt = langchain_core.prompts.ChatPromptTemplate.from_template(
        "Tell me a short joke about {topic} in the style of {style}"
    )

    chat_openai = langchain_openai.ChatOpenAI(temperature=0.7, n=1)
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

    chain.invoke({"topic": "chickens", "style": "a 90s rapper"})


@pytest.mark.asyncio
@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
async def test_lcel_chain_simple_async(langchain_core, langchain_openai, openai_completion):
    prompt = langchain_core.prompts.ChatPromptTemplate.from_messages(
        [("system", "You are world class technical documentation writer."), ("user", "{input}")]
    )
    llm = langchain_openai.OpenAI()

    chain = prompt | llm
    await chain.ainvoke({"input": "how can langsmith help with testing?"})


@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_lcel_chain_batch(langchain_core, langchain_openai, openai_chat_completion):
    """
    Test that invoking a chain with a batch of inputs will result in a 4-span trace,
    with a root RunnableSequence span, then 3 LangChain ChatOpenAI spans underneath
    """
    prompt = langchain_core.prompts.ChatPromptTemplate.from_template("Tell me a short joke about {topic}")
    output_parser = langchain_core.output_parsers.StrOutputParser()
    model = langchain_openai.ChatOpenAI(temperature=0.7, n=1)
    chain = {"topic": langchain_core.runnables.RunnablePassthrough()} | prompt | model | output_parser

    chain.batch(inputs=["chickens"])


@pytest.mark.snapshot(
    ignores=IGNORE_FIELDS,
    variants={
        "0_2": LANGCHAIN_VERSION < (0, 3),
        "latest": LANGCHAIN_VERSION >= (0, 3),
    },
)
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


@pytest.mark.asyncio
@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
async def test_lcel_chain_batch_async(langchain_core, langchain_openai, openai_chat_completion):
    """
    Test that invoking a chain with a batch of inputs will result in a 4-span trace,
    with a root RunnableSequence span, then 3 LangChain ChatOpenAI spans underneath
    """
    prompt = langchain_core.prompts.ChatPromptTemplate.from_template("Tell me a short joke about {topic}")
    output_parser = langchain_core.output_parsers.StrOutputParser()
    model = langchain_openai.ChatOpenAI(temperature=0.7, n=1)
    chain = {"topic": langchain_core.runnables.RunnablePassthrough()} | prompt | model | output_parser

    await chain.abatch(inputs=["chickens"])


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
def test_lcel_with_tools_openai(langchain_core, langchain_openai, openai_chat_completion_tools):
    import langchain_core.tools

    @langchain_core.tools.tool
    def add(a: int, b: int) -> int:
        """Adds a and b.

        Args:
            a: first int
            b: second int
        """
        return a + b

    llm = langchain_openai.ChatOpenAI(model="gpt-3.5-turbo-0125", temperature=0.7, n=1)
    llm_with_tools = llm.bind_tools([add])
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
        faiss = langchain_community.vectorstores.faiss.FAISS.from_texts(
            ["this is a test query."],
            embedding=langchain_openai.OpenAIEmbeddings(),
        )
        retriever = faiss.as_retriever()
        with request_vcr.use_cassette("openai_retrieval_embedding.yaml"):
            retriever.invoke("What was the message of the last test query?")


@pytest.mark.snapshot(
    ignores=(
        ["meta.langchain.request.openai-chat.parameters.n", "meta.langchain.request.openai-chat.parameters.temperature"]
        + IGNORE_FIELDS
    )
)
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


@pytest.mark.snapshot(
    ignores=(
        ["meta.langchain.request.openai-chat.parameters.n", "meta.langchain.request.openai-chat.parameters.temperature"]
        + IGNORE_FIELDS
    )
)
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
    ignores=(
        ["meta.langchain.request.openai-chat.parameters.n", "meta.langchain.request.openai-chat.parameters.temperature"]
        + IGNORE_FIELDS
    ),
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
    ignores=(
        ["meta.langchain.request.openai-chat.parameters.n", "meta.langchain.request.openai-chat.parameters.temperature"]
        + IGNORE_FIELDS
    ),
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


@pytest.mark.snapshot(
    ignores=(
        IGNORE_FIELDS
        + [
            "meta.langchain.request.inputs.0",
            "meta.langchain.request.openai-chat.parameters.max_completion_tokens",
            "meta.langchain.request.openai-chat.parameters.max_tokens",
        ]
    )
)
def test_streamed_json_output_parser(langchain, langchain_core, langchain_openai, streamed_response_responder):
    client = streamed_response_responder(
        module="openai",
        client_class_key="OpenAI",
        http_client_key="http_client",
        endpoint_path=["chat", "completions"],
        file="lcel_openai_chat_streamed_response_json_output_parser.txt",
    )

    model = langchain_openai.ChatOpenAI(model="gpt-4o", max_tokens=50, client=client, n=1, temperature=0.7)
    parser = langchain_core.output_parsers.JsonOutputParser()

    chain = model | parser
    inp = (
        "output a list of the country france their population in JSON format. "
        'Use a dict with an outer key of "countries" which contains a list of countries. '
        "Each country should have the key `name` and `population`"
    )

    messages = [
        langchain.schema.SystemMessage(content="You know everything about the world."),
        langchain.schema.HumanMessage(content=inp),
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
def test_base_tool_invoke(langchain_core):
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
async def test_base_tool_ainvoke(langchain_core):
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
def test_base_tool_invoke_non_json_serializable_config(langchain_core):
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
