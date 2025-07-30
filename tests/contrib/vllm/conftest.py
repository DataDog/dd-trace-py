import mock
import pytest

from ddtrace.contrib.internal.vllm.patch import patch
from ddtrace.contrib.internal.vllm.patch import unpatch
from ddtrace.llmobs import LLMObs
from ddtrace.trace import Pin
from tests.utils import DummyTracer
from tests.utils import DummyWriter
from tests.utils import override_config
from tests.utils import override_global_config


@pytest.fixture
def ddtrace_config_vllm():
    return {}


@pytest.fixture
def ddtrace_global_config():
    return {}


def default_global_config():
    return {"_dd_api_key": "<not-a-real-api_key>"}


@pytest.fixture
def mock_tracer(ddtrace_global_config, vllm):
    try:
        pin = Pin.get_from(vllm)
        mock_tracer = DummyTracer(writer=DummyWriter(trace_flush_enabled=False))
        pin._override(vllm, tracer=mock_tracer)
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


@pytest.fixture
def vllm(ddtrace_global_config, ddtrace_config_vllm):
    global_config = default_global_config()
    global_config.update(ddtrace_global_config)
    with override_global_config(global_config):
        with override_config("vllm", ddtrace_config_vllm):
            patch()
            try:
                import vllm
                yield vllm
            except ImportError:
                pytest.skip("vLLM not available")
            finally:
                unpatch() 