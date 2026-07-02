import os

import mock
import pydantic_ai
import pytest
import vcr

from ddtrace.contrib.internal.openai.patch import patch as patch_openai
from ddtrace.contrib.internal.openai.patch import unpatch as unpatch_openai
from ddtrace.contrib.internal.pydantic_ai.patch import patch
from ddtrace.contrib.internal.pydantic_ai.patch import unpatch
from ddtrace.internal.utils.version import parse_version
from ddtrace.llmobs import LLMObs as llmobs_service
from tests.utils import override_global_config


PYDANTIC_AI_VERSION = parse_version(pydantic_ai.__version__)
# The recorded OpenAI cassettes were captured against pydantic-ai <=1.63.0. The pydantic-ai ->
# OpenAI request/response wire format drifts across releases, and re-recording requires a live
# gateway, so VCR-replaying ``test_agent_run*`` tests (any test that uses ``request_vcr``) are
# skipped above this ceiling. The builder-only ``test_manifest_*`` tests need no cassette and still
# run, so a newer pin in riot exercises the version-tolerant manifest extraction (e.g. the retries
# attribute rename) on every release without a recorded fixture.
PYDANTIC_AI_VCR_CEILING = (1, 63, 0)


@pytest.fixture(autouse=True)
def _skip_vcr_above_ceiling(request):
    if "request_vcr" in request.fixturenames and PYDANTIC_AI_VERSION > PYDANTIC_AI_VCR_CEILING:
        pytest.skip(
            "VCR cassettes recorded against pydantic-ai <=%s; re-recording needs a live gateway"
            % (".".join(map(str, PYDANTIC_AI_VCR_CEILING)))
        )


def default_global_config():
    return {}


@pytest.fixture
def ddtrace_global_config():
    return {}


@pytest.fixture(autouse=True)
def pydantic_ai(ddtrace_global_config, monkeypatch):
    global_config = default_global_config()
    global_config.update(ddtrace_global_config)
    with override_global_config(global_config):
        monkeypatch.setenv("OPENAI_API_KEY", "<not-a-real-key>")
        patch()
        import pydantic_ai

        yield pydantic_ai
        unpatch()


@pytest.fixture
def openai_patched():
    patch_openai()
    import openai

    yield openai
    unpatch_openai()


@pytest.fixture
def pydantic_ai_llmobs(tracer, monkeypatch):
    llmobs_service.disable()
    with override_global_config(
        {
            "_llmobs_ml_app": "<ml-app-name>",
            "_dd_api_key": "<not-a-real-key>",
        }
    ):
        llmobs_service.enable(_tracer=tracer, integrations_enabled=False)
        # Replace the real LLMObsSpanWriter with a mock so we don't keep a
        # background flush thread alive trying to ship spans during the test.
        llmobs_service._instance._llmobs_span_writer.stop()
        llmobs_service._instance._llmobs_span_writer = mock.MagicMock()
        yield llmobs_service
    llmobs_service.disable()


@pytest.fixture
def request_vcr(ignore_localhost=True):
    return vcr.VCR(
        cassette_library_dir=os.path.join(os.path.dirname(__file__), "cassettes"),
        record_mode="once",
        match_on=["path"],
        filter_headers=["authorization", "x-api-key", "api-key"],
        ignore_localhost=ignore_localhost,
    )
