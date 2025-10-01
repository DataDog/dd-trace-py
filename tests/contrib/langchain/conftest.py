import importlib
import os

import pytest

from ddtrace._trace.pin import Pin
from ddtrace.contrib.internal.langchain.patch import patch as langchain_core_patch
from ddtrace.contrib.internal.langchain.patch import unpatch as langchain_core_unpatch
from ddtrace.internal.utils.version import parse_version
from ddtrace.llmobs import LLMObs as llmobs_service
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
def tracer(langchain_core):
    tracer = DummyTracer()

    pin = Pin.get_from(langchain_core)
    pin._override(langchain_core, tracer=tracer)

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


# scoping this fixture to "module" overcomes issues with patching ABC embeddings/vectorstore classes
# if scoped to each test/function, we will sometimes not patch those classes on __init_subclass__, likely
# due to how and when __init_subclass__ is called, and/or if the subclass being used in tests is cached in
# `sys.modules`. Additionally, changing to "session" will fail snapshot tests.
# Setting this to "module" seems to work the best.
# Additionally, this likely isn't an indication of a bug in our code, but how patching happens during tests.
@pytest.fixture(scope="module")
def langchain_core():
    with override_env(
        dict(
            OPENAI_API_KEY=os.getenv("OPENAI_API_KEY", "<not-a-real-key>"),
            ANTHROPIC_API_KEY=os.getenv("ANTHROPIC_API_KEY", "<not-a-real-key>"),
        )
    ):
        langchain_core_patch()
        import langchain_core

        yield langchain_core
        langchain_core_unpatch()


@pytest.fixture
def langchain_openai(langchain_core):
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
def langchain_cohere(langchain_core):
    try:
        import langchain_cohere

        yield langchain_cohere
    except ImportError:
        yield


@pytest.fixture
def langchain_anthropic(langchain_core):
    try:
        import langchain_anthropic

        yield langchain_anthropic
    except ImportError:
        yield


@pytest.fixture
def langchain_in_memory_vectorstore(langchain_core, langchain_openai, openai_url):
    if parse_version(importlib.metadata.version("langchain_core")) < (0, 3, 0):
        pytest.skip("langchain_core <0.3.0 does not support in-memory vectorstores")

    embedding = langchain_openai.OpenAIEmbeddings(base_url=openai_url)
    vectorstore = langchain_core.vectorstores.in_memory.InMemoryVectorStore(embedding=embedding)

    vectorstore.add_documents(
        [
            langchain_core.documents.Document(page_content="The capital of France is Paris."),
            langchain_core.documents.Document(page_content="The capital of Germany is Berlin."),
            langchain_core.documents.Document(page_content="A stop sign has 8 sides."),
        ]
    )

    yield vectorstore
