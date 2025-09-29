import importlib
import os
import weakref

import pytest

from ddtrace._trace.pin import Pin
from ddtrace.contrib.internal.vllm.patch import patch
from ddtrace.contrib.internal.vllm.patch import unpatch
from ddtrace.llmobs import LLMObs as llmobs_service
from tests.llmobs._utils import TestLLMObsSpanWriter
from tests.utils import DummyTracer
from tests.utils import DummyWriter
from tests.utils import override_global_config

from ._utils import log_vllm_diagnostics
from ._utils import shutdown_cached_llms


@pytest.fixture(scope="session", autouse=True)
def _shutdown_cached_llms_session():
    yield
    shutdown_cached_llms()


@pytest.fixture(autouse=True)
def _per_test_llm_cleanup():
    # After each test, try to free CUDA memory and check for strays
    yield
    try:
        import gc  # type: ignore

        gc.collect()
    except Exception:
        pass
    try:
        import torch  # type: ignore

        if hasattr(torch, "cuda"):
            torch.cuda.empty_cache()
    except Exception:
        pass
    if log_vllm_diagnostics is not None:
        try:
            log_vllm_diagnostics("after-per-test-cleanup")
        except Exception:
            pass


def pytest_runtest_makereport(item, call):  # type: ignore
    # When diagnostics enabled, log environment and memory on failures
    should_diag = item.config.getoption("-s", default=False) or item.config.getoption("--capture", default=None) in (
        "no",
        None,
    )
    # Also enable via env var to print regardless of -s
    import os as _os

    env_diag = _os.environ.get("DD_VLLM_TEST_DIAG") == "1"
    if call.when == "call" and (call.excinfo is not None) and (should_diag or env_diag):
        try:
            log_vllm_diagnostics(f"test-failure:{item.nodeid}")
        except Exception:
            pass


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
    with override_global_config({"_llmobs_ml_app": "<ml-app-name>", "service": "tests.contrib.vllm"}):
        llmobs_service.enable(_tracer=mock_tracer, integrations_enabled=False)
        llmobs_service._instance._llmobs_span_writer = llmobs_span_writer
        yield llmobs_service
    llmobs_service.disable()


@pytest.fixture
def llmobs_events(vllm_llmobs, llmobs_span_writer):
    return llmobs_span_writer.events


@pytest.fixture(scope="module")
def vllm_engine_mode():
    # Derive engine mode from environment for CI job split (default v1)
    mode = os.environ.get("VLLM_USE_V1", "1").strip()
    if mode not in {"0", "1"}:
        mode = "1"
    print(f"VLLM_USE_V1: {mode}")
    os.environ["VLLM_USE_V1"] = mode
    os.environ["DD_TRACE_DEBUG"] = "1"
    return mode


# Cached fixtures following vLLM's test patterns


@pytest.fixture(scope="module")
def opt_125m_llm(vllm_engine_mode):
    """Cached facebook/opt-125m LLM for text generation tests."""
    import vllm
    from vllm.distributed import cleanup_dist_env_and_memory

    llm = vllm.LLM(
        model="facebook/opt-125m",
        max_model_len=256,
        enforce_eager=True,
        compilation_config={"use_inductor": False},
        gpu_memory_utilization=0.1,
    )
    yield weakref.proxy(llm)
    del llm
    cleanup_dist_env_and_memory()


@pytest.fixture(scope="module")
def e5_small_llm(vllm_engine_mode):
    """Cached intfloat/e5-small LLM for embedding tests."""
    import vllm
    from vllm.distributed import cleanup_dist_env_and_memory

    llm = vllm.LLM(
        model="intfloat/e5-small",
        runner="pooling",
        max_model_len=256,
        enforce_eager=True,
        compilation_config={"use_inductor": False},
        trust_remote_code=True,
        gpu_memory_utilization=0.1,
    )
    yield weakref.proxy(llm)
    del llm
    cleanup_dist_env_and_memory()


@pytest.fixture(scope="module")
def bge_reranker_llm(vllm_engine_mode):
    """Cached BAAI/bge-reranker-v2-m3 LLM for classification/ranking tests."""
    import vllm
    from vllm.distributed import cleanup_dist_env_and_memory

    llm = vllm.LLM(
        model="BAAI/bge-reranker-v2-m3",
        runner="pooling",
        max_model_len=256,
        enforce_eager=True,
        compilation_config={"use_inductor": False},
        trust_remote_code=True,
        gpu_memory_utilization=0.1,
    )
    yield weakref.proxy(llm)
    del llm
    cleanup_dist_env_and_memory()
