import os

import pytest

from ddtrace.contrib.internal.langchain.patch import patch
from ddtrace.contrib.internal.langchain.patch import unpatch
from ddtrace.llmobs import LLMObs as llmobs_service
from ddtrace.trace import Pin
from tests.llmobs._utils import TestLLMObsSpanWriter
from tests.utils import DummyTracer
from tests.utils import override_env
from tests.utils import override_global_config


@pytest.fixture
def llmobs_env():
    return {
        "DD_API_KEY": "<default-not-a-real-key>",
        "DD_LLMOBS_ML_APP": "unnamed-ml-app",
    }


@pytest.fixture
def llmobs_span_writer():
    yield TestLLMObsSpanWriter(1.0, 5.0, is_agentless=True, _site="datad0g.com", _api_key="<not-a-real-key>")


@pytest.fixture
def tracer(langchain):
    tracer = DummyTracer()
    pin = Pin.get_from(langchain)
    pin._override(langchain, tracer=tracer)
    yield tracer


@pytest.fixture
def llmobs(
    tracer,
    llmobs_span_writer,
):
    with override_global_config(dict(_dd_api_key="<not-a-real-key>", _llmobs_proxy_urls="http://localhost:4000")):
        llmobs_service.enable(_tracer=tracer, ml_app="langchain_test", integrations_enabled=False)
        llmobs_service._instance._llmobs_span_writer = llmobs_span_writer
        yield llmobs_service
        llmobs_service.disable()


@pytest.fixture
def llmobs_events(llmobs, llmobs_span_writer):
    yield llmobs_span_writer.events


@pytest.fixture
def langchain():
    with override_env(
        dict(
            OPENAI_API_KEY=os.getenv("OPENAI_API_KEY", "<not-a-real-key>"),
            COHERE_API_KEY=os.getenv("COHERE_API_KEY", "<not-a-real-key>"),
            ANTHROPIC_API_KEY=os.getenv("ANTHROPIC_API_KEY", "<not-a-real-key>"),
            HUGGINGFACEHUB_API_TOKEN=os.getenv("HUGGINGFACEHUB_API_TOKEN", "<not-a-real-key>"),
            AI21_API_KEY=os.getenv("AI21_API_KEY", "<not-a-real-key>"),
        )
    ):
        patch()
        import langchain

        yield langchain
        unpatch()


@pytest.fixture
def langchain_community(langchain):
    try:
        import langchain_community

        yield langchain_community
    except ImportError:
        yield


@pytest.fixture
def langchain_core(langchain):
    import langchain_core
    import langchain_core.prompts  # noqa: F401

    yield langchain_core


@pytest.fixture
def langchain_openai(langchain):
    try:
        import langchain_openai

        yield langchain_openai
    except ImportError:
        yield


@pytest.fixture
def langchain_cohere(langchain):
    try:
        import langchain_cohere

        yield langchain_cohere
    except ImportError:
        yield


@pytest.fixture
def langchain_anthropic(langchain):
    try:
        import langchain_anthropic

        yield langchain_anthropic
    except ImportError:
        yield


@pytest.fixture
def langchain_pinecone(langchain):
    with override_env(
        dict(
            PINECONE_API_KEY=os.getenv("PINECONE_API_KEY", "<not-a-real-key>"),
        )
    ):
        try:
            import langchain_pinecone

            yield langchain_pinecone
        except ImportError:
            yield


@pytest.fixture
def streamed_response_responder():
    try:
        import importlib
        import os

        import httpx

        class CustomTransport(httpx.BaseTransport):
            def __init__(self, file: str):
                super().__init__()
                self.file = file

            def handle_request(self, request: httpx.Request) -> httpx.Response:
                with open(
                    os.path.join(os.path.dirname(__file__), f"cassettes/{self.file}"),
                    "r",
                    encoding="utf-8",
                ) as f:
                    content = f.read()
                    return httpx.Response(200, request=request, content=content)

        def responder(module, client_class_key, http_client_key, endpoint_path: list[str], file: str):
            # endpoint_path specified the specific endpoint to retrieve as a client off of the general client
            # ie, ["chat", "completions"] would represent openai.chat.completions
            clientModule = importlib.import_module(module)  # openai, anthropic, etc.
            client_class = getattr(clientModule, client_class_key)
            client = client_class(**{http_client_key: httpx.Client(transport=CustomTransport(file=file))})

            for prop in endpoint_path:
                client = getattr(client, prop)

            return client

        yield responder

    except ImportError:
        yield


@pytest.fixture
def async_streamed_response_responder():
    try:
        import importlib
        import os

        import httpx

        class CustomTransport(httpx.AsyncBaseTransport):
            def __init__(self, file: str):
                super().__init__()
                self.file = file

            async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
                with open(
                    os.path.join(os.path.dirname(__file__), f"cassettes/{self.file}"),
                    "r",
                    encoding="utf-8",
                ) as f:
                    content = f.read()
                    return httpx.Response(200, request=request, content=content)

        def responder(module, client_class_key, http_client_key, endpoint_path: list[str], file: str):
            # endpoint_path specified the specific endpoint to retrieve as a client off of the general client
            # ie, ["chat", "completions"] would represent openai.chat.completions
            clientModule = importlib.import_module(module)  # openai, anthropic, etc.
            client_class = getattr(clientModule, client_class_key)
            client = client_class(**{http_client_key: httpx.AsyncClient(transport=CustomTransport(file=file))})

            for prop in endpoint_path:
                client = getattr(client, prop)

            return client

        yield responder

    except ImportError:
        yield


def _openai_completion_object(
    n: int = 1,
):
    from datetime import datetime

    from openai.types.completion import Completion
    from openai.types.completion_choice import CompletionChoice
    from openai.types.completion_usage import CompletionUsage

    choice = CompletionChoice(
        text="I am a helpful assistant.",
        index=0,
        logprobs=None,
        finish_reason="length",
    )

    choices = [choice for _ in range(n)]

    completion = Completion(
        id="foo",
        model="gpt-3.5-turbo-instruct",
        object="text_completion",
        choices=choices,
        created=int(datetime.now().timestamp()),
        usage=CompletionUsage(
            prompt_tokens=5,
            completion_tokens=5,
            total_tokens=10,
        ),
    )

    return completion


def _openai_chat_completion_object(
    n: int = 1,
    tools: bool = False,
):
    from datetime import datetime

    from openai.types.chat import ChatCompletionMessage
    from openai.types.chat.chat_completion import ChatCompletion
    from openai.types.chat.chat_completion import Choice
    from openai.types.completion_usage import CompletionUsage

    choice = Choice(
        finish_reason="stop",
        index=0,
        message=ChatCompletionMessage(
            content="Hello world!",
            role="assistant",
        ),
    )
    choices = [choice for _ in range(n)]

    completion = ChatCompletion(
        id="foo",
        model="gpt-4",
        object="chat.completion",
        choices=choices,
        created=int(datetime.now().timestamp()),
        usage=CompletionUsage(
            prompt_tokens=5,
            completion_tokens=5,
            total_tokens=10,
        ),
    )

    if tools:
        from openai.types.chat.chat_completion_message_tool_call import ChatCompletionMessageToolCall
        from openai.types.chat.chat_completion_message_tool_call import Function

        tool_call = ChatCompletionMessageToolCall(
            function=Function(
                arguments='{"a":1,"b":2}',
                name="add",
            ),
            id="bar",
            type="function",
        )

        for choice in completion.choices:
            choice.message.tool_calls = [tool_call]

    return completion


@pytest.fixture
@pytest.mark.respx()
def openai_completion(respx_mock):
    completion = _openai_completion_object()

    import httpx

    respx_mock.post("/v1/completions").mock(return_value=httpx.Response(200, json=completion.model_dump(mode="json")))


@pytest.fixture
@pytest.mark.respx()
def openai_completion_multiple(respx_mock):
    import httpx

    completion = _openai_completion_object(n=2)

    respx_mock.post("/v1/completions").mock(return_value=httpx.Response(200, json=completion.model_dump(mode="json")))


@pytest.fixture
@pytest.mark.respx()
def openai_completion_error(respx_mock):
    import httpx

    respx_mock.post("/v1/completions").mock(
        return_value=httpx.Response(
            400,
            json={
                "error": {
                    "message": "Invalid token in prompt: 123. Minimum value is 0, maximum value is 100257 (inclusive).",
                    "type": "invalid_request_error",
                    "param": None,
                    "code": None,
                }
            },
        )
    )


@pytest.fixture
@pytest.mark.respx()
def openai_chat_completion(respx_mock):
    import httpx

    completion = _openai_chat_completion_object()

    respx_mock.post("/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=completion.model_dump(mode="json"))
    )


@pytest.fixture
@pytest.mark.respx()
def openai_chat_completion_multiple(respx_mock):
    import httpx

    completion = _openai_chat_completion_object(n=2)

    respx_mock.post("/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=completion.model_dump(mode="json"))
    )


@pytest.fixture
@pytest.mark.respx()
def openai_chat_completion_tools(respx_mock):
    import httpx

    completion = _openai_chat_completion_object(tools=True)

    respx_mock.post("/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=completion.model_dump(mode="json"))
    )


@pytest.fixture
@pytest.mark.respx()
def openai_embedding(respx_mock):
    import httpx
    from openai.types.embedding import Embedding

    embedding = Embedding(
        embedding=[0.1, 0.2, 0.3],
        index=0,
        object="embedding",
    )

    respx_mock.post("/v1/embeddings").mock(return_value=httpx.Response(200, json=embedding.model_dump(mode="json")))
