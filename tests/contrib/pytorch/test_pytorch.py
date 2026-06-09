"""Integration tests for the pytorch.rank lifetime span.

These tests exercise the real patch/unpatch cycle with a CPU-only gloo
process group and assert that a ``pytorch.rank`` span is emitted with the
expected tags (rank, world_size, framework, training_job_id).
"""

import os
import sys

import pytest
import torch


# torch.distributed.init_process_group cannot be called more than once per
# process on torch < 2.1; re-init hangs indefinitely with the gloo backend.
pytestmark = pytest.mark.skipif(
    tuple(int(x) for x in torch.__version__.split(".")[:2]) < (2, 1),
    reason="distributed re-init hangs on torch<2.1",
)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    """Reset integration state before each test."""
    from ddtrace.contrib.internal.pytorch import _distributed
    from ddtrace.contrib.internal.pytorch import _test_helpers as _th
    from ddtrace.contrib.internal.pytorch.patch import unpatch as pt_unpatch

    _th.close_rank_root()
    _th.reset_device_cache()
    _distributed._installed = False
    _distributed._state.update({"bootstrapped": False, "job_id": None, "rank": 0, "world_size": 1})
    setattr(__import__("torch"), "_datadog_patch", False)
    yield
    try:
        pt_unpatch()
    except Exception:
        pass
    _th.close_rank_root()
    _th.reset_device_cache()


def _setup_single_rank_gloo():
    os.environ.setdefault("MASTER_ADDR", "127.0.0.1")
    os.environ.setdefault("MASTER_PORT", "29555")
    os.environ.setdefault("RANK", "0")
    os.environ.setdefault("WORLD_SIZE", "1")
    if not torch.distributed.is_initialized():
        torch.distributed.init_process_group(backend="gloo", rank=0, world_size=1)


def _teardown_gloo():
    if torch.distributed.is_initialized():
        torch.distributed.destroy_process_group()


def test_rank_span_emitted_on_init_process_group(monkeypatch, test_spans):
    """patch() + init_process_group emits a ``pytorch.rank`` span with the
    correct rank, world_size, framework, and training_job_id tags.
    The span is closed by destroy_process_group (the wrapped version) or
    unpatch(), so we tear down before inspecting spans.
    """
    monkeypatch.setenv("TORCHELASTIC_RUN_ID", "test-run-123")

    from ddtrace.contrib.internal.pytorch.patch import patch as pt_patch
    from ddtrace.contrib.internal.pytorch.patch import unpatch as pt_unpatch

    pt_patch()
    _setup_single_rank_gloo()
    try:
        _teardown_gloo()
    finally:
        pt_unpatch()

    spans = test_spans.pop()
    rank_spans = [s for s in spans if s.name == "pytorch.rank"]
    assert rank_spans, "no pytorch.rank span emitted"
    span = rank_spans[0]
    assert span.get_metric("rank") == 0
    assert span.get_metric("world_size") == 1
    assert span.get_tag("framework") is not None
    assert span.get_tag("training_job.id") is not None


def test_rank_span_job_id_from_torchelastic_env(monkeypatch, test_spans):
    """When TORCHELASTIC_RUN_ID is set the rank span carries that value as
    training_job.id.
    """
    monkeypatch.setenv("TORCHELASTIC_RUN_ID", "elastic-run-99")
    monkeypatch.delenv("DD_PYTORCH_JOB_ID", raising=False)

    from ddtrace.contrib.internal.pytorch.patch import patch as pt_patch
    from ddtrace.contrib.internal.pytorch.patch import unpatch as pt_unpatch

    pt_patch()
    _setup_single_rank_gloo()
    try:
        _teardown_gloo()
    finally:
        pt_unpatch()

    spans = test_spans.pop()
    rank_spans = [s for s in spans if s.name == "pytorch.rank"]
    assert rank_spans, "no pytorch.rank span emitted"
    span = rank_spans[0]
    assert span.get_tag("training_job.id") == "elastic-run-99"


def test_fsdp_not_eagerly_imported():
    """patch(pytorch=True) must NOT cause torch.distributed.fsdp to land in
    sys.modules. Eagerly importing it pulls _dynamo + sympy (~1.3 s startup
    overhead) for every DDP workload that never touches FSDP.
    """
    for _key in list(sys.modules):
        if _key == "torch.distributed.fsdp" or _key.startswith("torch.distributed.fsdp."):
            sys.modules.pop(_key)

    from ddtrace.contrib.internal.pytorch.patch import patch as pt_patch
    from ddtrace.contrib.internal.pytorch.patch import unpatch as pt_unpatch

    try:
        pt_patch()
        assert "torch.distributed.fsdp" not in sys.modules, (
            "_install_fsdp() imported torch.distributed.fsdp eagerly — convert it to register_post_import_hook"
        )
    finally:
        pt_unpatch()


def test_fsdp_wrapper_installed_on_import():
    """After patch(), importing torch.distributed.fsdp should trigger the
    post-import hook and wrap FullyShardedDataParallel.__init__.
    """
    for _key in list(sys.modules):
        if _key == "torch.distributed.fsdp" or _key.startswith("torch.distributed.fsdp."):
            sys.modules.pop(_key)

    from ddtrace.contrib.internal.pytorch.patch import patch as pt_patch
    from ddtrace.contrib.internal.pytorch.patch import unpatch as pt_unpatch

    try:
        pt_patch()
        # Trigger the hook by importing the module.
        try:
            from torch.distributed.fsdp import FullyShardedDataParallel
        except ImportError as e:
            pytest.skip(f"torch.distributed.fsdp not importable in this environment: {e}")

        assert hasattr(FullyShardedDataParallel.__init__, "__wrapped__"), (
            "FSDP.__init__ was not wrapped after post-import hook fired"
        )
    finally:
        pt_unpatch()


def test_bootstrap_reads_ray_env_vars(monkeypatch):
    """_bootstrap_distributed() must populate the run-metadata cache from
    Ray-set env vars so that pytorch.rank spans carry ray.submission_id and
    ray.train.run_name tags.
    """
    monkeypatch.setenv("_RAY_SUBMISSION_ID", "raysubmit_xyz")
    monkeypatch.setenv("_RAY_JOB_NAME", "my-experiment")
    monkeypatch.setenv("RAY_JOB_ID", "33000000")

    from ddtrace.contrib.internal.pytorch import _utils
    from ddtrace.contrib.internal.pytorch._distributed import _populate_ray_run_metadata

    _utils.clear_cached_run_metadata()
    _populate_ray_run_metadata()

    rm = _utils.get_cached_run_metadata()
    assert rm.get("submission_id") == "raysubmit_xyz"
    assert rm.get("run_name") == "my-experiment"
