import gc
import weakref

import pytest
import torch

from ddtrace.contrib.internal.vllm.patch import patch
from ddtrace.contrib.internal.vllm.patch import unpatch
from ddtrace.llmobs import LLMObs as llmobs_service
from tests.llmobs._utils import TestLLMObsSpanWriter
from tests.utils import override_global_config

from ._utils import shutdown_cached_llms


@pytest.fixture(scope="session", autouse=True)
def _shutdown_cached_llms_session():
    yield
    shutdown_cached_llms()


@pytest.fixture(autouse=True)
def _per_test_llm_cleanup():
    """Free CUDA memory after each test."""
    yield
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


@pytest.fixture(autouse=True, scope="session")
def require_gpu():
    """Skip vLLM tests if GPU is not available."""
    if not (hasattr(torch, "cuda") and torch.cuda.is_available()):
        pytest.skip("Skipping vLLM tests: GPU not available")


@pytest.fixture()
def vllm():
    patch()
    import vllm

    yield vllm
    unpatch()


@pytest.fixture
def llmobs_span_writer():
    yield TestLLMObsSpanWriter(1.0, 5.0, is_agentless=True, _site="datad0g.com")


@pytest.fixture
def vllm_llmobs(tracer, llmobs_span_writer):
    llmobs_service.disable()
    with override_global_config({"_llmobs_ml_app": "<ml-app-name>", "service": "tests.contrib.vllm"}):
        llmobs_service.enable(_tracer=tracer, integrations_enabled=False)
        llmobs_service._instance._llmobs_span_writer = llmobs_span_writer
        yield llmobs_service
    llmobs_service.disable()


@pytest.fixture
def llmobs_events(vllm_llmobs, llmobs_span_writer):
    return llmobs_span_writer.events


@pytest.fixture(scope="module")
def opt_125m_llm():
    """Cached facebook/opt-125m LLM for text generation tests."""
    # Ensure patching happens before LLM creation
    from ddtrace.contrib.internal.vllm.patch import patch

    patch()

    import vllm
    from vllm.distributed import cleanup_dist_env_and_memory

    llm = vllm.LLM(
        model="facebook/opt-125m",
        max_model_len=256,
        enforce_eager=True,
        gpu_memory_utilization=0.1,
    )
    yield weakref.proxy(llm)
    del llm
    cleanup_dist_env_and_memory()


@pytest.fixture(scope="module")
def e5_small_llm():
    """Cached intfloat/e5-small LLM for embedding tests."""
    # Ensure patching happens before LLM creation
    from ddtrace.contrib.internal.vllm.patch import patch

    patch()

    import vllm
    from vllm.distributed import cleanup_dist_env_and_memory

    llm = vllm.LLM(
        model="intfloat/e5-small",
        runner="pooling",
        max_model_len=256,
        enforce_eager=True,
        trust_remote_code=True,
        gpu_memory_utilization=0.1,
    )
    yield weakref.proxy(llm)
    del llm
    cleanup_dist_env_and_memory()


@pytest.fixture(scope="module")
def bge_reranker_llm():
    """Cached BAAI/bge-reranker-v2-m3 LLM for classification/ranking tests."""
    # Ensure patching happens before LLM creation
    from ddtrace.contrib.internal.vllm.patch import patch

    patch()

    import vllm
    from vllm.distributed import cleanup_dist_env_and_memory

    llm = vllm.LLM(
        model="BAAI/bge-reranker-v2-m3",
        runner="pooling",
        max_model_len=256,
        enforce_eager=True,
        trust_remote_code=True,
        gpu_memory_utilization=0.1,
    )
    yield weakref.proxy(llm)
    del llm
    cleanup_dist_env_and_memory()
