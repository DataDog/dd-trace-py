import pytest

from ._utils import shutdown_cached_llms
import importlib
import pytest

from ddtrace._trace.pin import Pin
from ddtrace.contrib.internal.vllm.patch import patch
from ddtrace.contrib.internal.vllm.patch import unpatch
from ddtrace.llmobs import LLMObs as llmobs_service
from tests.llmobs._utils import TestLLMObsSpanWriter
from ddtrace.llmobs import LLMObs
from tests.utils import DummyTracer
from tests.utils import DummyWriter
from tests.utils import override_global_config


@pytest.fixture(scope="session", autouse=True)
def _shutdown_cached_llms_session():
    yield
    shutdown_cached_llms()


@pytest.fixture(autouse=True, scope="session")
def require_gpu():
    # Only run vLLM tests when a GPU is available (CI and local parity)
    try:
        torch = importlib.import_module("torch")
        if not (hasattr(torch, "cuda") and torch.cuda.is_available()):
            pytest.skip("Skipping vLLM tests: GPU not available")
    except Exception:
        pytest.skip("Skipping vLLM tests: torch not installed")
    return

@pytest.fixture()
def vllm():
    patch()
    import vllm
    yield vllm
    unpatch()


@pytest.fixture
def mock_tracer(vllm):
    pin = Pin.get_from(vllm)
    mock_tracer = DummyTracer(writer=DummyWriter(trace_flush_enabled=False))
    pin._override(vllm, tracer=mock_tracer)
    yield mock_tracer


@pytest.fixture
def llmobs_span_writer():
    yield TestLLMObsSpanWriter(1.0, 5.0, is_agentless=True, _site="datad0g.com")


@pytest.fixture
def vllm_llmobs(mock_tracer, llmobs_span_writer):
    llmobs_service.disable()
    with override_global_config(
        {"_llmobs_ml_app": "<ml-app-name>", "service": "tests.contrib.vllm"}
    ):
        llmobs_service.enable(_tracer=mock_tracer, integrations_enabled=False)
        llmobs_service._instance._llmobs_span_writer = llmobs_span_writer
        yield llmobs_service
    llmobs_service.disable()


@pytest.fixture
def llmobs_events(vllm_llmobs, llmobs_span_writer):
    return llmobs_span_writer.events


@pytest.fixture(params=["0", "1"])
def vllm_engine_mode(request, monkeypatch):
    # Parametrize tests to run under both V0 and V1 engines
    monkeypatch.setenv("VLLM_USE_V1", request.param)
    monkeypatch.setenv("DD_TRACE_DEBUG", "1")
    return request.param