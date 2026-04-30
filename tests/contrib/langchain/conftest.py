import importlib
import os
from unittest import mock

import pytest

from ddtrace.contrib.internal.langchain.patch import patch as langchain_core_patch
from ddtrace.contrib.internal.langchain.patch import unpatch as langchain_core_unpatch
from ddtrace.internal.utils.version import parse_version
from ddtrace.llmobs import LLMObs
from tests.utils import override_env
from tests.utils import override_global_config


@pytest.fixture
def llmobs_env():
    return {
        "DD_API_KEY": "<default-not-a-real-key>",
        "DD_LLMOBS_ML_APP": "unnamed-ml-app",
    }


@pytest.fixture
def ddtrace_global_config():
    return {}


@pytest.fixture
def test_spans(ddtrace_global_config, test_spans, monkeypatch):
    """Override of the shared ``test_spans`` fixture that, when LLMObs is enabled
    via ``ddtrace_global_config``, sets ``_DD_LLMOBS_TEST_KEEP_META_STRUCT=1`` so
    ``meta_struct["_llmobs"]`` is preserved on spans for assertion via
    ``_get_llmobs_data_metastruct``. The langchain integration also needs the
    instrumented-proxy-URL config in place at ``LLMObs.enable()`` time, so the
    override is applied via ``override_global_config`` here.
    """
    try:
        if ddtrace_global_config.get("_llmobs_enabled", False):
            monkeypatch.setenv("_DD_LLMOBS_TEST_KEEP_META_STRUCT", "1")
            with override_global_config(ddtrace_global_config):
                LLMObs.disable()
                LLMObs.enable(
                    _tracer=test_spans.tracer,
                    ml_app="langchain_test",
                    integrations_enabled=False,
                )
                LLMObs._instance._llmobs_span_writer.stop()
                LLMObs._instance._llmobs_span_writer = mock.MagicMock()
                yield test_spans
        else:
            yield test_spans
    finally:
        LLMObs.disable()


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
            GOOGLE_API_KEY=os.getenv("GOOGLE_API_KEY", "<not-a-real-key>"),
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
def langchain_google_genai(langchain_core):
    try:
        import langchain_google_genai

        yield langchain_google_genai
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
