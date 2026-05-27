"""Regression tests for PyTorch integration edge cases:

* ``install()`` / ``uninstall()`` must be idempotent (no wrapper stacking).
* Layer One must read ``group`` from positional args (not just kwargs).
* Exceptions raised inside a wrapped collective must not leak spans.
"""

import socket
from unittest import mock

import pytest
import torch
import torch.distributed as dist

from ddtrace.contrib.internal.pytorch import _device
from ddtrace.contrib.internal.pytorch import _distributed
from ddtrace.contrib.internal.pytorch import _test_helpers as _th
from ddtrace.contrib.internal.pytorch import patch as pytorch_patch


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _force_clean_wraps() -> None:
    """Defensively remove any pytorch wraps left by earlier tests in this
    session. Earlier tests (e.g. ``test_layer_one_gating``) call
    ``_distributed.install()`` directly, bypassing the
    ``torch._datadog_patch`` flag, so the high-level ``unpatch()`` returns
    early and leaves wrappers attached. Force ``_installed = True`` and call
    ``uninstall()`` to walk the canonical teardown path.
    """
    setattr(torch, "_datadog_patch", False)
    _distributed._installed = True
    try:
        _distributed.uninstall()
    except Exception:
        pass


@pytest.fixture
def _clean_state(monkeypatch):
    _force_clean_wraps()
    _th.reset_device_cache()
    _th.reset_metrics_state()
    _th.close_rank_root()
    yield
    _force_clean_wraps()
    _th.reset_device_cache()
    _th.reset_metrics_state()
    _th.close_rank_root()


def _wrapper_depth(fn) -> int:
    depth = 0
    while hasattr(fn, "__wrapped__"):
        depth += 1
        fn = fn.__wrapped__
    return depth


def test_install_is_idempotent_no_wrapper_stacking(_clean_state):
    """Calling install() twice must not stack wrappers on torch.distributed."""
    assert _wrapper_depth(torch.distributed.all_reduce) == 0
    _distributed.install()
    depth_after_first = _wrapper_depth(torch.distributed.all_reduce)
    assert depth_after_first == 1
    _distributed.install()  # must be a no-op
    assert _wrapper_depth(torch.distributed.all_reduce) == depth_after_first
    _distributed.uninstall()
    assert _wrapper_depth(torch.distributed.all_reduce) == 0


def test_patch_unpatch_patch_cycle_is_clean(_clean_state):
    """A full patch/unpatch/patch cycle must leave exactly one wrapper layer."""
    pytorch_patch.patch()
    depth_after_first = _wrapper_depth(torch.distributed.all_reduce)
    pytorch_patch.unpatch()
    assert _wrapper_depth(torch.distributed.all_reduce) == 0
    pytorch_patch.patch()
    assert _wrapper_depth(torch.distributed.all_reduce) == depth_after_first


def test_uninstall_is_idempotent(_clean_state):
    """uninstall() without a prior install() is a no-op."""
    _distributed.uninstall()
    _distributed.uninstall()


def test_collective_wrapper_reads_group_positionally(monkeypatch, _clean_state):
    """The Layer One CUDA-event gate must see a positionally-passed group."""
    seen: dict = {}

    def fake_should_record(group, tensor):
        seen["group"] = group
        return False

    monkeypatch.setattr(_distributed, "_should_record_cuda_event", fake_should_record)
    from ddtrace import config

    monkeypatch.setattr(config.pytorch, "collective_trace_enabled", True)
    _distributed._state["resolver"] = mock.Mock()

    wrapper = _distributed._make_collective_wrapper("pytorch.allreduce", tensor_arg_index=0, group_arg_index=2)

    sentinel_group = mock.Mock(name="ProcessGroup")

    def fake_all_reduce(tensor, op=None, group=None, async_op=False):
        return None

    wrapper(fake_all_reduce, None, (torch.zeros(2), None, sentinel_group), {})
    assert seen["group"] is sentinel_group


def test_collective_wrapper_prefers_kwarg_group(monkeypatch, _clean_state):
    """When the caller passes group= as a kwarg the kwarg wins."""
    seen: dict = {}

    def fake_should_record(group, tensor):
        seen["group"] = group
        return False

    monkeypatch.setattr(_distributed, "_should_record_cuda_event", fake_should_record)
    from ddtrace import config

    monkeypatch.setattr(config.pytorch, "collective_trace_enabled", True)
    _distributed._state["resolver"] = mock.Mock()

    wrapper = _distributed._make_collective_wrapper("pytorch.allreduce", tensor_arg_index=0, group_arg_index=2)

    kwarg_group = mock.Mock(name="ProcessGroup-kwarg")

    def fake_all_reduce(*args, **kwargs):
        return None

    wrapper(fake_all_reduce, None, (torch.zeros(2),), {"group": kwarg_group})
    assert seen["group"] is kwarg_group


def test_exception_in_collective_finishes_span_and_records_metric(monkeypatch, _clean_state, tracer, test_spans):
    """When the inner collective raises, Layer One must finish the span (with
    error tagged) and Layer Zero must still emit the metric so dashboards
    don't go blind during failures.
    """
    from ddtrace import config

    # `_metrics.record_collective` gates on `_device.get()` returning a
    # discovered device, so seed it before exercising the wrapper.
    with (
        mock.patch.object(_device, "_cuda_is_available", return_value=False),
        mock.patch.object(_device, "_hostname", return_value="h-exc"),
    ):
        _device.discover(local_rank=0)

    monkeypatch.setattr(config.pytorch, "collective_trace_enabled", True)
    fake = mock.Mock()
    _th.install_metrics_client(fake)

    wrapper = _distributed._make_collective_wrapper("pytorch.allreduce", tensor_arg_index=0, group_arg_index=2)

    class _Boom(RuntimeError):
        pass

    def explode(*args, **kwargs):
        raise _Boom("collective failed")

    with pytest.raises(_Boom):
        wrapper(explode, None, (torch.zeros(4),), {})

    duration_calls = [c for c in fake.distribution.call_args_list if c.args[0] == "collective.duration_ms"]
    assert duration_calls, "Layer Zero metric not emitted on exception"

    spans = test_spans.get_spans()
    allreduce_spans = [s for s in spans if s.name == "pytorch.allreduce"]
    assert allreduce_spans, "Layer One span not produced"
    span = allreduce_spans[0]
    assert span.duration is not None, "span not finished on exception"
    assert span.error == 1, "span should be tagged as errored"


def test_real_allreduce_positional_group_is_traced(monkeypatch, _clean_state, test_spans):
    """End-to-end: passing the default group positionally to a real
    `dist.all_reduce` still flows through the wrapper without skipping the
    Layer Zero metric or losing the Layer One span.
    """
    from ddtrace import config

    monkeypatch.setattr(config.pytorch, "collective_trace_enabled", True)
    fake = mock.Mock()
    _th.install_metrics_client(fake)

    with (
        mock.patch.object(_device, "_cuda_is_available", return_value=False),
        mock.patch.object(_device, "_hostname", return_value="h-pos"),
    ):
        _device.discover(local_rank=0)
        pytorch_patch.patch()

    port = _free_port()
    monkeypatch.setenv("MASTER_ADDR", "127.0.0.1")
    monkeypatch.setenv("MASTER_PORT", str(port))
    dist.init_process_group(backend="gloo", rank=0, world_size=1)
    try:
        t = torch.zeros(4)
        # `group` passed positionally (index 2 in all_reduce signature).
        dist.all_reduce(t, torch.distributed.ReduceOp.SUM, None)
    finally:
        dist.destroy_process_group()

    assert any(c.args[0] == "collective.duration_ms" for c in fake.distribution.call_args_list)
    assert any(s.name == "pytorch.allreduce" for s in test_spans.get_spans())
