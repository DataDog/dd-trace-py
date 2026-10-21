import gc
import os
import shutil
import sys
from unittest import mock
import weakref

from ._tiny_models import MODELS_DIR
from ._tiny_models import TINY_BERT
from ._tiny_models import TINY_OPT
from ._tiny_models import TINY_RERANKER
from ._tiny_models import write_models


# The tests load tiny local models with load_format="dummy", so vLLM needs only the config and tokenizer files
# that write_models creates. HF_HUB_OFFLINE=1 makes sure that the tests never call the Hugging Face Hub:
# anonymous Hub requests from CI are rate limited. huggingface_hub reads this variable when it is imported, and
# the vLLM engine core subprocess inherits it.
assert "huggingface_hub" not in sys.modules, "huggingface_hub was imported before HF_HUB_OFFLINE was set"
os.environ["HF_HUB_OFFLINE"] = "1"
write_models()

import pytest  # noqa: E402
import torch  # noqa: E402

from ddtrace.contrib.internal.vllm.patch import patch  # noqa: E402
from ddtrace.contrib.internal.vllm.patch import unpatch  # noqa: E402
from ddtrace.llmobs import LLMObs  # noqa: E402
from tests.utils import override_global_config  # noqa: E402

from ._utils import shutdown_cached_llms  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _shutdown_cached_llms_session():
    yield
    shutdown_cached_llms()
    shutil.rmtree(MODELS_DIR, ignore_errors=True)


@pytest.fixture(autouse=True)
def _per_test_llm_cleanup():
    """Free CUDA memory after each test."""
    yield
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "no_gpu: mark a vLLM test that does not load a model and can run without a GPU",
    )


def _skip_if_no_gpu():
    if not (hasattr(torch, "cuda") and torch.cuda.is_available()):
        pytest.skip("Skipping vLLM tests: GPU not available")


@pytest.fixture(autouse=True)
def require_gpu(request):
    """Skip vLLM tests if GPU is not available.

    Tests marked no_gpu (pure module/patch resolution checks that load no
    model) run regardless of GPU availability. Function scope is required so
    the per-test marker can be honored -- a session-scoped fixture can't see
    individual test markers.
    """
    if request.node.get_closest_marker("no_gpu"):
        return
    _skip_if_no_gpu()


@pytest.fixture(scope="module")
def _require_gpu_module():
    """Module-scoped GPU gate for the cached LLM fixtures below.

    pytest can't let a module-scoped fixture depend on the function-scoped
    require_gpu fixture (ScopeMismatch), and instantiating fixtures follows
    scope breadth, not declaration order -- without this, the module-scoped
    LLM fixtures below would try to construct a GPU-backed vllm.LLM before
    require_gpu ever runs on a machine without a GPU.
    """
    _skip_if_no_gpu()


@pytest.fixture()
def vllm():
    patch()
    import vllm

    yield vllm
    unpatch()


@pytest.fixture
def vllm_llmobs(tracer, monkeypatch):
    LLMObs.disable()
    with override_global_config(
        {
            "_llmobs_ml_app": "<ml-app-name>",
            "_dd_api_key": "<not-a-real-key>",
            "service": "tests.contrib.vllm",
        }
    ):
        LLMObs.enable(_tracer=tracer, integrations_enabled=False)
        # Replace the real LLMObsSpanWriter with a mock so we don't keep a
        # background flush thread alive trying to ship spans during the test.
        LLMObs._instance._llmobs_span_writer.stop()
        LLMObs._instance._llmobs_span_writer = mock.MagicMock()
        yield LLMObs
    LLMObs.disable()


@pytest.fixture(scope="module")
def tiny_opt_llm(_require_gpu_module):
    """Cached tiny OPT LLM for text generation tests."""
    # Ensure patching happens before LLM creation
    from ddtrace.contrib.internal.vllm.patch import patch

    patch()

    import vllm
    from vllm.distributed import cleanup_dist_env_and_memory

    llm = vllm.LLM(
        model=TINY_OPT,
        # The tokenizer makes one token for each byte, so prompts have more tokens than with a real tokenizer.
        max_model_len=512,
        enforce_eager=True,
        load_format="dummy",
        gpu_memory_utilization=0.1,
    )
    yield weakref.proxy(llm)
    del llm
    cleanup_dist_env_and_memory()


@pytest.fixture(scope="module")
def tiny_bert_llm(_require_gpu_module):
    """Cached tiny BERT LLM for embedding tests."""
    # Ensure patching happens before LLM creation
    from ddtrace.contrib.internal.vllm.patch import patch

    patch()

    import vllm
    from vllm.distributed import cleanup_dist_env_and_memory

    llm = vllm.LLM(
        model=TINY_BERT,
        runner="pooling",
        max_model_len=256,
        enforce_eager=True,
        load_format="dummy",
        trust_remote_code=True,
        gpu_memory_utilization=0.1,
    )
    yield weakref.proxy(llm)
    del llm
    cleanup_dist_env_and_memory()


@pytest.fixture(scope="module")
def tiny_reranker_llm(_require_gpu_module):
    """Cached tiny XLM-RoBERTa reranker LLM for classification/ranking tests."""
    # Ensure patching happens before LLM creation
    from ddtrace.contrib.internal.vllm.patch import patch

    patch()

    import vllm
    from vllm.distributed import cleanup_dist_env_and_memory

    llm = vllm.LLM(
        model=TINY_RERANKER,
        runner="pooling",
        max_model_len=256,
        enforce_eager=True,
        load_format="dummy",
        trust_remote_code=True,
        gpu_memory_utilization=0.1,
    )
    yield weakref.proxy(llm)
    del llm
    cleanup_dist_env_and_memory()
