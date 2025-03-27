import os
import mock

import pytest
from ddtrace.contrib.internal.litellm.patch import patch
from ddtrace.trace import Pin
from ddtrace.contrib.internal.litellm.patch import unpatch
from tests.utils import DummyTracer
from tests.utils import DummyWriter
from tests.utils import override_config
from tests.utils import override_env
from tests.utils import override_global_config
from tests.contrib.litellm.utils import get_request_vcr
from ddtrace.llmobs import LLMObs


def default_global_config():
    return {}


@pytest.fixture
def ddtrace_global_config():
    return {}


@pytest.fixture
def ddtrace_config_litellm():
    return {}


@pytest.fixture()
def mock_llmobs_writer():
    patcher = mock.patch("ddtrace.llmobs._llmobs.LLMObsSpanWriter")
    try:
        LLMObsSpanWriterMock = patcher.start()
        m = mock.MagicMock()
        LLMObsSpanWriterMock.return_value = m
        yield m
    finally:
        patcher.stop()

@pytest.fixture
def litellm(ddtrace_global_config, ddtrace_config_litellm):
    global_config = default_global_config()
    global_config.update(ddtrace_global_config)
    with override_global_config(global_config):
        with override_config("litellm", ddtrace_config_litellm):
            with override_env(
                dict(
                    OPENAI=os.getenv("OPENAI_API_KEY", "<not-a-real-key>"),
                )
            ):
                patch()
                import litellm

                yield litellm
                unpatch()

@pytest.fixture
def mock_tracer(litellm, ddtrace_global_config):
    pin = Pin.get_from(litellm)
    mock_tracer = DummyTracer(writer=DummyWriter(trace_flush_enabled=False))
    pin._override(litellm, tracer=mock_tracer)
    pin.tracer._configure()

    if ddtrace_global_config.get("_llmobs_enabled", False):
        # Have to disable and re-enable LLMObs to use to mock tracer.
        LLMObs.disable()
        LLMObs.enable(_tracer=mock_tracer, integrations_enabled=False)

    yield mock_tracer

    LLMObs.disable()


@pytest.fixture
def request_vcr():
    return get_request_vcr()
