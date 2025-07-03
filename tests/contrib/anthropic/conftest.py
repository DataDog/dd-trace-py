import os

import mock
import pytest

from ddtrace.contrib.internal.anthropic.patch import patch
from ddtrace.contrib.internal.anthropic.patch import unpatch
from ddtrace.llmobs import LLMObs
from ddtrace.trace import Pin
from tests.contrib.anthropic.utils import get_request_vcr
from tests.utils import DummyTracer
from tests.utils import DummyWriter
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
def snapshot_tracer(anthropic):
    pin = Pin.get_from(anthropic)
    yield pin.tracer


@pytest.fixture
def mock_tracer(ddtrace_global_config, anthropic):
    try:
        pin = Pin.get_from(anthropic)
        mock_tracer = DummyTracer(writer=DummyWriter(trace_flush_enabled=False))
        pin._override(anthropic, tracer=mock_tracer)
        if ddtrace_global_config.get("_llmobs_enabled", False):
            # Have to disable and re-enable LLMObs to use to mock tracer.
            LLMObs.disable()
            LLMObs.enable(_tracer=mock_tracer, integrations_enabled=False)
        yield mock_tracer
    finally:
        LLMObs.disable()


@pytest.fixture
def mock_llmobs_writer(scope="session"):
    patcher = mock.patch("ddtrace.llmobs._llmobs.LLMObsSpanWriter")
    try:
        LLMObsSpanWriterMock = patcher.start()
        m = mock.MagicMock()
        LLMObsSpanWriterMock.return_value = m
        yield m
    finally:
        patcher.stop()


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
