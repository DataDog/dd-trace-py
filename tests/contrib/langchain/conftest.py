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
    with override_global_config(
        dict(_dd_api_key="<not-a-real-key>", _llmobs_instrumented_proxy_urls="http://localhost:4000")
    ):
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
            # OPENAI_API_KEY=os.getenv("OPENAI_API_KEY", "<not-a-real-key>"),
            # COHERE_API_KEY=os.getenv("COHERE_API_KEY", "<not-a-real-key>"),
            # ANTHROPIC_API_KEY=os.getenv("ANTHROPIC_API_KEY", "<not-a-real-key>"),
            # HUGGINGFACEHUB_API_TOKEN=os.getenv("HUGGINGFACEHUB_API_TOKEN", "<not-a-real-key>"),
            # AI21_API_KEY=os.getenv("AI21_API_KEY", "<not-a-real-key>"),
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
def openai_url() -> str:
    """
    Use the request recording endpoint of the testagent to capture requests to OpenAI
    """
    return "http://localhost:9126/vcr/openai"


@pytest.fixture
def anthropic_url() -> str:
    """
    Use the request recording endpoint of the testagent to capture the requests to Anthropic
    """
    return "http://localhost:9126/vcr/anthropic"


@pytest.fixture
def cohere_url() -> str:
    """
    Use the request recording endpoint of the testagent to capture the requests to Cohere
    """
    return "http://localhost:9126/vcr/cohere"


@pytest.fixture
def ai21_url() -> str:
    """
    Use the request recording endpoint of the testagent to capture the requests to AI21
    """
    return "http://localhost:9126/vcr/ai21"


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
