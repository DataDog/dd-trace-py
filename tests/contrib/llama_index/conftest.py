"""Pytest fixtures for llama_index LLMObs tests.

These fixtures follow the pattern from tests/contrib/anthropic/conftest.py.
"""

import os

import mock
import pytest

from ddtrace.contrib.internal.llama_index.patch import patch
from ddtrace.contrib.internal.llama_index.patch import unpatch
from ddtrace.llmobs import LLMObs
from tests.contrib.llama_index.utils import get_request_vcr
from tests.utils import override_config
from tests.utils import override_env
from tests.utils import override_global_config


@pytest.fixture
def ddtrace_config_llama_index():
    """Fixture providing llama_index configuration."""
    return {}


@pytest.fixture
def ddtrace_global_config():
    """Fixture providing global ddtrace configuration."""
    return {}


@pytest.fixture
def test_spans(ddtrace_global_config, test_spans):
    """Fixture managing test spans and LLMObs state."""
    try:
        if ddtrace_global_config.get("_llmobs_enabled", False):
            # Have to disable and re-enable LLMObs to use to mock tracer.
            LLMObs.disable()
            LLMObs.enable(integrations_enabled=False)
        yield test_spans
    finally:
        LLMObs.disable()


@pytest.fixture
def mock_llmobs_writer(scope="session"):
    """Fixture providing mocked LLMObs writer."""
    patcher = mock.patch("ddtrace.llmobs._llmobs.LLMObsSpanWriter")
    try:
        llmobsspanwritermock = patcher.start()
        m = mock.MagicMock()
        llmobsspanwritermock.return_value = m
        yield m
    finally:
        patcher.stop()


def default_global_config():
    """Get default global configuration for tests."""
    return {
        "_dd_api_key": "<not-a-real-api_key>",
        "service": "tests.contrib.llama_index",
        "version": "",
        "env": "",
    }


@pytest.fixture
def llama_index(ddtrace_global_config, ddtrace_config_llama_index):
    """Fixture providing patched llama_index module."""
    global_config = default_global_config()
    global_config.update(ddtrace_global_config)
    # Clear environment variables that could affect the service name
    with override_env(
        {
            "DD_SERVICE": "",
            "DD_VERSION": "",
            "DD_ENV": "",
        }
    ):
        with override_global_config(global_config):
            with override_config("llama_index", ddtrace_config_llama_index):
                patch()
                import llama_index.core

                yield llama_index.core
                unpatch()


@pytest.fixture(scope="session")
def request_vcr():
    """Fixture providing VCR instance for recording/replaying API responses."""
    yield get_request_vcr()


@pytest.fixture
def openai_llm(llama_index):
    """Fixture providing an OpenAI LLM instance via llama-index.

    Requires OPENAI_API_KEY environment variable for recording new cassettes.
    """
    from llama_index.llms.openai import OpenAI

    api_key = os.getenv("OPENAI_API_KEY", "<not-a-real-key>")
    return OpenAI(model="gpt-4o-mini", api_key=api_key, temperature=0.0)


@pytest.fixture
def openai_embedding(llama_index):
    """Fixture providing an OpenAI Embedding instance via llama-index.

    Requires OPENAI_API_KEY environment variable for recording new cassettes.
    """
    from llama_index.embeddings.openai import OpenAIEmbedding

    api_key = os.getenv("OPENAI_API_KEY", "<not-a-real-key>")
    return OpenAIEmbedding(model="text-embedding-3-small", api_key=api_key)


@pytest.fixture
def openai_custom_llm(llama_index):
    """Fixture providing an OpenAI-backed CustomLLM that returns token usage.

    This fixture creates the class AFTER llama_index is patched.

    IMPORTANT: We only override complete() and stream_complete(), NOT chat().
    CustomLLM.chat() calls self.complete() internally, so:
    1. User calls llm.chat() -> goes through our wrapped CustomLLM.chat()
    2. CustomLLM.chat() calls self.complete() -> our overridden complete()
    3. Token usage from complete() propagates back through chat()

    Requires OPENAI_API_KEY environment variable for recording new cassettes.
    """
    from typing import Any

    from llama_index.core.llms import CompletionResponse
    from llama_index.core.llms import CustomLLM
    from llama_index.core.llms import LLMMetadata
    from llama_index.core.llms.callbacks import llm_completion_callback
    import openai

    class OpenAICustomLLM(CustomLLM):
        """Custom LLM that wraps OpenAI and properly returns token usage.

        Only overrides complete() - chat() is inherited from CustomLLM (which we wrap).
        CustomLLM.chat() internally calls self.complete(), so the token usage flows through.
        """

        model: str = "gpt-4o-mini"
        api_key: str = ""
        temperature: float = 0.0

        @property
        def metadata(self) -> LLMMetadata:
            return LLMMetadata(model_name=self.model)

        @llm_completion_callback()
        def complete(self, prompt: str, formatted: bool = False, **kwargs: Any) -> CompletionResponse:
            """Complete using OpenAI API and return response with token usage."""
            client = openai.OpenAI(api_key=self.api_key)

            response = client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=self.temperature,
            )

            content = response.choices[0].message.content or ""
            usage = response.usage

            return CompletionResponse(
                text=content,
                additional_kwargs={
                    "usage": {
                        "prompt_tokens": usage.prompt_tokens if usage else 0,
                        "completion_tokens": usage.completion_tokens if usage else 0,
                        "total_tokens": usage.total_tokens if usage else 0,
                    }
                },
            )

        @llm_completion_callback()
        def stream_complete(self, prompt: str, formatted: bool = False, **kwargs: Any) -> Any:
            """Stream complete - not implemented, just satisfies abstract method."""
            raise NotImplementedError("stream_complete not implemented for test LLM")

        @property
        def _model_name(self) -> str:
            return self.model

    api_key = os.getenv("OPENAI_API_KEY", "<not-a-real-key>")
    return OpenAICustomLLM(model="gpt-4o-mini", api_key=api_key, temperature=0.0)
