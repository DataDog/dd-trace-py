import importlib
from operator import itemgetter

import pytest

from ddtrace.internal.utils.version import parse_version


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


@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_openai_llm_sync(langchain_openai, openai_url):
    llm = langchain_openai.OpenAI(base_url=openai_url)
    llm.invoke("Can you explain what Descartes meant by 'I think, therefore I am'?")


@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_openai_llm_sync_multiple_prompts(langchain_openai, openai_url):
    llm = langchain_openai.OpenAI(base_url=openai_url)
    llm.generate(
        prompts=[
            "What is the best way to teach a baby multiple languages?",
            "How many times has Spongebob failed his road test?",
        ]
    )


@pytest.mark.asyncio
@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
async def test_openai_llm_async(langchain_openai, openai_url):
    llm = langchain_openai.OpenAI(base_url=openai_url)
    await llm.agenerate(["Which team won the 2019 NBA finals?"])


@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_openai_llm_error(langchain_openai, openai_url):
    import openai  # Imported here because the os env OPENAI_API_KEY needs to be set via langchain fixture before import

    llm = langchain_openai.OpenAI(base_url=openai_url)

    with pytest.raises(openai.BadRequestError):
        llm.generate([12345, 123456])


@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_openai_chat_model_sync_call_langchain_openai(langchain_core, langchain_openai, openai_url):
    chat = langchain_openai.ChatOpenAI(temperature=0, max_tokens=256, n=1, base_url=openai_url)
    chat.invoke(input=[langchain_core.messages.HumanMessage(content="When do you use 'whom' instead of 'who'?")])


@pytest.mark.asyncio
@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
async def test_openai_chat_model_sync_call_langchain_openai_async(langchain_core, langchain_openai, openai_url):
    chat = langchain_openai.ChatOpenAI(temperature=0, max_tokens=256, n=1, base_url=openai_url)
    await chat.ainvoke(input=[langchain_core.messages.HumanMessage(content="When do you use 'whom' instead of 'who'?")])


# TODO: come back and clean this one up... seems like we tag 4 responses
@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_openai_chat_model_sync_generate(langchain_core, langchain_openai, openai_url):
    if parse_version(langchain_core.__version__) < (0, 3, 0):
        pytest.skip("langchain-core <0.3.0 does not support stream_usage=False")
    chat = langchain_openai.ChatOpenAI(temperature=0, max_tokens=256, base_url=openai_url)
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


def test_openai_chat_model_vision_generate(langchain_core, langchain_openai, openai_url):
    """
    Test that input messages with nested contents are still tagged without error
    Regression test for https://github.com/DataDog/dd-trace-py/issues/8149.
    """
    image_url = (
        "https://upload.wikimedia.org/wikipedia/commons/thumb/d/dd/Gfp-wisconsin-madison-the-nature-boardwalk"
        ".jpg/2560px-Gfp-wisconsin-madison-the-nature-boardwalk.jpg"
    )
    chat = langchain_openai.ChatOpenAI(model="gpt-4o", temperature=0, max_tokens=256, base_url=openai_url)
    chat.generate(
        [
            [
                langchain_core.messages.HumanMessage(
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


@pytest.mark.asyncio
@pytest.mark.snapshot(
    ignores=IGNORE_FIELDS
    + [
        # async batch requests can result in a non-deterministic order
        "meta.langchain.response.completions.0.0.content",
        "meta.langchain.response.completions.1.0.content",
    ]
)
async def test_openai_chat_model_async_generate(langchain_core, langchain_openai, openai_url):
    if parse_version(langchain_core.__version__) < (0, 3, 0):
        pytest.skip("Bug in langchain: https://github.com/langchain-ai/langgraph/issues/136")
    chat = langchain_openai.ChatOpenAI(temperature=0, max_tokens=256, base_url=openai_url)
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


@pytest.mark.snapshot
def test_openai_embedding_query(langchain_openai, openai_url):
    embeddings = langchain_openai.embeddings.OpenAIEmbeddings(base_url=openai_url)
    embeddings.embed_query(text="foo")


@pytest.mark.snapshot
def test_openai_embedding_document(langchain_openai, openai_url):
    embeddings = langchain_openai.embeddings.OpenAIEmbeddings(base_url=openai_url)
    embeddings.embed_documents(texts=["foo", "bar"])


@pytest.mark.snapshot
def test_vectorstore_similarity_search(langchain_in_memory_vectorstore):
    vectorstore = langchain_in_memory_vectorstore
    vectorstore.similarity_search("What is the capital of France?", k=1)


@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_lcel_chain_simple(langchain_core, langchain_openai, openai_url):
    prompt = langchain_core.prompts.ChatPromptTemplate.from_messages(
        [("system", "You are world class technical documentation writer."), ("user", "{input}")]
    )
    llm = langchain_openai.OpenAI(base_url=openai_url)

    chain = prompt | llm
    chain.invoke({"input": "how can langsmith help with testing?"})


@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_lcel_chain_complicated(langchain_core, langchain_openai, openai_url):
    prompt = langchain_core.prompts.ChatPromptTemplate.from_template(
        "Tell me a short joke about {topic} in the style of {style}"
    )

    chat_openai = langchain_openai.ChatOpenAI(temperature=0.7, n=1, base_url=openai_url)
    openai = langchain_openai.OpenAI(base_url=openai_url)

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
async def test_lcel_chain_simple_async(langchain_core, langchain_openai, openai_url):
    prompt = langchain_core.prompts.ChatPromptTemplate.from_messages(
        [("system", "You are world class technical documentation writer."), ("user", "{input}")]
    )
    llm = langchain_openai.OpenAI(base_url=openai_url)

    chain = prompt | llm
    await chain.ainvoke({"input": "how can langsmith help with testing?"})


@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_lcel_chain_batch(langchain_core, langchain_openai, openai_url):
    """
    Test that invoking a chain with a batch of inputs will result in a 4-span trace,
    with a root RunnableSequence span, then 3 LangChain ChatOpenAI spans underneath
    """
    prompt = langchain_core.prompts.ChatPromptTemplate.from_template("Tell me a short joke about {topic}")
    output_parser = langchain_core.output_parsers.StrOutputParser()
    model = langchain_openai.ChatOpenAI(temperature=0.7, n=1, base_url=openai_url)
    chain = {"topic": langchain_core.runnables.RunnablePassthrough()} | prompt | model | output_parser

    chain.batch(inputs=["chickens"])


def test_lcel_chain_nested(langchain_core, langchain_openai, openai_url):
    """
    Test that invoking a nested chain will result in a 4-span trace with a root
    RunnableSequence span (complete_chain), then another RunnableSequence (chain1) +
    LangChain ChatOpenAI span (chain1's llm call) and finally a second LangChain ChatOpenAI span (chain2's llm call)
    """
    prompt1 = langchain_core.prompts.ChatPromptTemplate.from_template("what is the city {person} is from?")
    prompt2 = langchain_core.prompts.ChatPromptTemplate.from_template(
        "what country is the city {city} in? respond in {language}"
    )

    model = langchain_openai.ChatOpenAI(base_url=openai_url)

    chain1 = prompt1 | model | langchain_core.output_parsers.StrOutputParser()
    chain2 = prompt2 | model | langchain_core.output_parsers.StrOutputParser()

    complete_chain = {"city": chain1, "language": itemgetter("language")} | chain2
    complete_chain.invoke({"person": "Spongebob Squarepants", "language": "Spanish"})


@pytest.mark.asyncio
@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
async def test_lcel_chain_batch_async(langchain_core, langchain_openai, openai_url):
    """
    Test that invoking a chain with a batch of inputs will result in a 4-span trace,
    with a root RunnableSequence span, then 3 LangChain ChatOpenAI spans underneath
    """
    prompt = langchain_core.prompts.ChatPromptTemplate.from_template("Tell me a short joke about {topic}")
    output_parser = langchain_core.output_parsers.StrOutputParser()
    model = langchain_openai.ChatOpenAI(temperature=0.7, n=1, base_url=openai_url)
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
def test_lcel_with_tools_openai(langchain_core, langchain_openai, openai_url):
    @langchain_core.tools.tool
    def add(a: int, b: int) -> int:
        """Adds a and b.

        Args:
            a: first int
            b: second int
        """
        return a + b

    llm = langchain_openai.ChatOpenAI(model="gpt-3.5-turbo-0125", temperature=0.7, n=1, base_url=openai_url)
    llm_with_tools = llm.bind_tools([add])
    llm_with_tools.invoke("What is the sum of 1 and 2?")


@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_lcel_with_tools_anthropic(langchain_core, langchain_anthropic, anthropic_url):
    if parse_version(importlib.metadata.version("langchain_anthropic")) < (0, 2, 0):
        pytest.skip("langchain-anthropic <0.2.0 does not support tools outside of a beta function")

    @langchain_core.tools.tool
    def add(a: int, b: int) -> int:
        """Adds a and b.

        Args:
            a: first int
            b: second int
        """
        return a + b

    kwargs = dict(
        temperature=1,
        model_name="claude-3-opus-20240229",
    )

    if "anthropic_api_url" in langchain_anthropic.ChatAnthropic.__fields__:
        kwargs["anthropic_api_url"] = anthropic_url
    else:
        kwargs["base_url"] = anthropic_url

    llm = langchain_anthropic.ChatAnthropic(**kwargs)
    llm_with_tools = llm.bind_tools([add])
    llm_with_tools.invoke("What is the sum of 1 and 2?")


@pytest.mark.snapshot(
    ignores=(
        ["meta.langchain.request.openai-chat.parameters.n", "meta.langchain.request.openai-chat.parameters.temperature"]
        + IGNORE_FIELDS
    )
)
def test_streamed_chain(langchain_core, langchain_openai, openai_url):
    prompt = langchain_core.prompts.ChatPromptTemplate.from_messages(
        [("system", "You are world class technical documentation writer."), ("user", "{input}")]
    )
    llm = langchain_openai.ChatOpenAI(base_url=openai_url)
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
def test_streamed_chat(langchain_openai, openai_url):
    model = langchain_openai.ChatOpenAI(base_url=openai_url)

    for _ in model.stream(input="how can langsmith help with testing?"):
        pass


@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_streamed_llm(langchain_openai, openai_url):
    llm = langchain_openai.OpenAI(base_url=openai_url)

    for _ in llm.stream(input="How do I write technical documentation?"):
        pass


@pytest.mark.snapshot(
    ignores=(
        ["meta.langchain.request.openai-chat.parameters.n", "meta.langchain.request.openai-chat.parameters.temperature"]
        + IGNORE_FIELDS
    ),
    token="tests.contrib.langchain.test_langchain.test_streamed_chain",
)
async def test_astreamed_chain(langchain_core, langchain_openai, openai_url):
    prompt = langchain_core.prompts.ChatPromptTemplate.from_messages(
        [("system", "You are world class technical documentation writer."), ("user", "{input}")]
    )
    llm = langchain_openai.ChatOpenAI(base_url=openai_url)
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
async def test_astreamed_chat(langchain_openai, openai_url):
    model = langchain_openai.ChatOpenAI(base_url=openai_url)

    async for _ in model.astream(input="how can langsmith help with testing?"):
        pass


@pytest.mark.snapshot(
    ignores=IGNORE_FIELDS,
    token="tests.contrib.langchain.test_langchain.test_streamed_llm",
)
async def test_astreamed_llm(langchain_openai, openai_url):
    llm = langchain_openai.OpenAI(base_url=openai_url)

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
def test_streamed_json_output_parser(langchain_core, langchain_openai, openai_url):
    model = langchain_openai.ChatOpenAI(model="gpt-4o", max_tokens=50, base_url=openai_url, n=1, temperature=0.7)
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
async def test_astreamed_events_does_not_throw(langchain_openai, langchain_core, openai_url):
    model = langchain_openai.ChatOpenAI(base_url=openai_url)
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

    def circumference_tool(radius: float) -> float:
        return float(radius) * 2.0 * pi

    calculator = langchain_core.tools.StructuredTool.from_function(
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

    def circumference_tool(radius: float) -> float:
        return float(radius) * 2.0 * pi

    calculator = langchain_core.tools.StructuredTool.from_function(
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

    def circumference_tool(radius: float) -> float:
        return float(radius) * 2.0 * pi

    calculator = langchain_core.tools.StructuredTool.from_function(
        func=circumference_tool,
        name="Circumference calculator",
        description="Use this tool when you need to calculate a circumference using the radius of a circle",
        return_direct=True,
        response_format="content",
    )

    calculator.invoke("2", config={"unserializable": object()})
