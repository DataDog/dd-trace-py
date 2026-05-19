import os
from typing import TYPE_CHECKING  # noqa:F401
from typing import Optional  # noqa:F401

import mock
import pytest

from ddtrace.contrib.internal.openai.patch import patch
from ddtrace.contrib.internal.openai.patch import unpatch
from ddtrace.llmobs import LLMObs
from ddtrace.trace import TraceFilter
from tests.utils import override_global_config


if TYPE_CHECKING:
    from ddtrace.trace import Span  # noqa:F401


def pytest_configure(config):
    config.addinivalue_line("markers", "vcr_logs(*args, **kwargs): mark test to have logs requests recorded")


@pytest.fixture
def api_key_in_env():
    return True


@pytest.fixture
def request_api_key(api_key_in_env, openai_api_key):
    """
    OpenAI allows both using an env var or client param for the API key, so this fixture specifies the API key
    (or None) to be used in the actual request param. If the API key is set as an env var, this should return None
    to make sure the env var will be used.
    """
    if api_key_in_env:
        return None
    return openai_api_key


@pytest.fixture
def openai_api_key():
    return os.getenv("OPENAI_API_KEY", "<not-a-real-key>")


@pytest.fixture
def openai_organization():
    return None


@pytest.fixture
def openai(openai_api_key, openai_organization, api_key_in_env):
    import openai

    if api_key_in_env:
        openai.api_key = openai_api_key
    # When testing locally to generate new cassette files, comment the line below to use the real OpenAI API key.
    os.environ["OPENAI_API_KEY"] = "<not-a-real-key>"
    openai.organization = openai_organization
    patch()
    yield openai
    unpatch()


@pytest.fixture
def azure_openai_config(openai):
    config = {
        "api_version": "2023-07-01-preview",
        "azure_endpoint": "https://test-openai.openai.azure.com/",
        "azure_deployment": "test-openai",
        "api_key": "<not-a-real-key>",
    }
    return config


class FilterOrg(TraceFilter):
    """Replace the organization tag on spans with fake data."""

    def process_trace(self, trace: list["Span"]) -> Optional[list["Span"]]:
        for span in trace:
            if span.get_tag("organization"):
                span._set_attribute("organization", "not-a-real-org")
        return trace


@pytest.fixture
def snapshot_tracer(tracer, openai):
    tracer.configure(trace_processors=[FilterOrg()])
    return tracer


@pytest.fixture
def openai_llmobs(snapshot_tracer, monkeypatch):
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
            _tracer=snapshot_tracer,
            integrations_enabled=False,
            instrumented_proxy_urls={"http://localhost:4000"},
        )
        # Replace the real LLMObsSpanWriter with a mock so we don't keep a
        # background flush thread alive trying to ship spans during the test.
        LLMObs._instance._llmobs_span_writer.stop()
        LLMObs._instance._llmobs_span_writer = mock.MagicMock()
        yield LLMObs
    LLMObs.disable()
