import os

import mock
import pytest

from ddtrace.contrib.internal.anthropic.patch import patch
from ddtrace.contrib.internal.anthropic.patch import unpatch
from ddtrace.llmobs import LLMObs
from tests.contrib.anthropic.utils import get_request_vcr
from tests.utils import override_config
from tests.utils import override_env
from tests.utils import override_global_config


@pytest.fixture
def ddtrace_config_anthropic():
    return {}


@pytest.fixture
def ddtrace_global_config():
    return {}


@pytest.fixture
def anthropic_llmobs(tracer, monkeypatch):
    # Preserve meta_struct["_llmobs"] on spans so tests can assert against
    # LLMObsSpanData via _get_llmobs_data_metastruct; production scrubs it after
    # enqueueing to LLMObsSpanWriter.
    monkeypatch.setenv("_DD_LLMOBS_TEST_KEEP_META_STRUCT", "1")
    LLMObs.disable()
    with override_global_config(
        {
            "_llmobs_ml_app": "<ml-app-name>",
            "_dd_api_key": "<not-a-real-key>",
        }
    ):
        LLMObs.enable(
            _tracer=tracer,
            integrations_enabled=False,
            instrumented_proxy_urls={"http://localhost:4000"},
        )
        # Replace the real LLMObsSpanWriter with a mock so we don't keep a
        # background flush thread alive trying to ship spans during the test.
        LLMObs._instance._llmobs_span_writer.stop()
        LLMObs._instance._llmobs_span_writer = mock.MagicMock()
        yield LLMObs
    LLMObs.disable()


def default_global_config():
    return {"_dd_api_key": "<not-a-real-api_key>"}


@pytest.fixture
def anthropic(ddtrace_global_config, ddtrace_config_anthropic):
    global_config = default_global_config()
    global_config.update(ddtrace_global_config)
    with override_global_config(global_config):
        with override_config("anthropic", ddtrace_config_anthropic):
            with override_env(
                dict(
                    ANTHROPIC_API_KEY=os.getenv("ANTHROPIC_API_KEY", "<not-a-real-key>"),
                )
            ):
                patch()
                import anthropic

                yield anthropic
                unpatch()


@pytest.fixture(scope="session")
def request_vcr():
    yield get_request_vcr()
