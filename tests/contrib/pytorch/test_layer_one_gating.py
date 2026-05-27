"""Default-off gating of Layer One (per-collective span emission)."""

import importlib
import socket
from unittest import mock

import pytest
import torch
import torch.distributed as dist

from ddtrace.contrib.internal.pytorch import _device
from ddtrace.contrib.internal.pytorch import _test_helpers as _th
from ddtrace.contrib.internal.pytorch import patch as pytorch_patch


def _reload_pytorch_config(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("DD_PYTORCH_COLLECTIVE_TRACE", raising=False)
    else:
        monkeypatch.setenv("DD_PYTORCH_COLLECTIVE_TRACE", value)
    import ddtrace.contrib.internal.pytorch as pytorch_mod

    importlib.reload(pytorch_mod)
    from ddtrace import config

    return config.pytorch


def test_collective_trace_defaults_false(monkeypatch):
    cfg = _reload_pytorch_config(monkeypatch, None)
    assert cfg.collective_trace_enabled is False


@pytest.mark.parametrize(
    "raw,expected", [("true", True), ("True", True), ("1", True), ("false", False), ("0", False), ("", False)]
)
def test_collective_trace_respects_env(monkeypatch, raw, expected):
    cfg = _reload_pytorch_config(monkeypatch, raw)
    assert cfg.collective_trace_enabled is expected


@pytest.fixture
def _setup_single_rank(monkeypatch):
    _th.reset_device_cache()
    _th.reset_metrics_state()
    with (
        mock.patch.object(_device, "_cuda_is_available", return_value=False),
        mock.patch.object(_device, "_hostname", return_value="h-7"),
    ):
        _device.discover(local_rank=0)
    pytorch_patch.patch()
    yield
    pytorch_patch.unpatch()
    _th.reset_device_cache()
    _th.reset_metrics_state()


def test_layer_one_off_emits_metrics_but_no_span(monkeypatch, _setup_single_rank, tracer, test_spans):
    # AIDEV-NOTE: We exercise the *installed* wrap (via the real
    # `torch.distributed.all_reduce` entry point) rather than calling
    # `_make_collective_wrapper` directly, so this test catches install-
    # path bugs (e.g. the wrap not being applied at all). A 1-rank gloo
    # process group is sufficient to make `all_reduce` succeed locally.
    from ddtrace import config

    monkeypatch.setattr(config.pytorch, "collective_trace_enabled", False)
    fake = mock.Mock()
    _th.install_metrics_client(fake)
    port = _free_port_for_test()
    monkeypatch.setenv("MASTER_ADDR", "127.0.0.1")
    monkeypatch.setenv("MASTER_PORT", str(port))
    dist.init_process_group(backend="gloo", rank=0, world_size=1)
    try:
        t = torch.zeros(4)
        dist.all_reduce(t)
    finally:
        dist.destroy_process_group()
    assert any(c.args[0] == "collective.duration_ms" for c in fake.distribution.call_args_list)
    spans = test_spans.get_spans()
    assert not any(s.name == "pytorch.allreduce" for s in spans)


def test_layer_one_on_emits_both_span_and_metric(monkeypatch, _setup_single_rank, tracer, test_spans):
    from ddtrace import config

    monkeypatch.setattr(config.pytorch, "collective_trace_enabled", True)
    fake = mock.Mock()
    _th.install_metrics_client(fake)
    port = _free_port_for_test()
    monkeypatch.setenv("MASTER_ADDR", "127.0.0.1")
    monkeypatch.setenv("MASTER_PORT", str(port))
    dist.init_process_group(backend="gloo", rank=0, world_size=1)
    try:
        t = torch.zeros(4)
        dist.all_reduce(t)
    finally:
        dist.destroy_process_group()
    spans = test_spans.get_spans()
    assert any(s.name == "pytorch.allreduce" for s in spans)
    assert any(c.args[0] == "collective.duration_ms" for c in fake.distribution.call_args_list)


def _free_port_for_test() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def test_bootstrap_opens_rank_root_and_starts_ticker(monkeypatch):
    from ddtrace.contrib.internal.pytorch import _distributed

    _th.reset_device_cache()
    _th.reset_metrics_state()
    _th.close_rank_root()

    with (
        mock.patch.object(_device, "_cuda_is_available", return_value=False),
        mock.patch.object(_device, "_hostname", return_value="h-1"),
    ):
        _distributed._state.update({"bootstrapped": False, "rank": 0, "world_size": 1, "job_id": "job-X"})
        with mock.patch.object(torch.distributed, "is_initialized", return_value=False):
            _distributed._bootstrap_distributed()

    assert _th.current_rank_span() is not None
    assert _distributed._state.get("rate_ticker") is not None
    assert _distributed._state["rate_ticker"]._thread.is_alive()


def test_uninstall_closes_rank_root_and_stops_ticker(monkeypatch):
    from ddtrace.contrib.internal.pytorch import _distributed

    _th.reset_device_cache()
    _th.reset_metrics_state()
    with (
        mock.patch.object(_device, "_cuda_is_available", return_value=False),
        mock.patch.object(_device, "_hostname", return_value="h-1"),
    ):
        _distributed._state.update({"bootstrapped": False, "rank": 0, "world_size": 1, "job_id": "job-X"})
        with mock.patch.object(torch.distributed, "is_initialized", return_value=False):
            _distributed._bootstrap_distributed()
    ticker = _distributed._state["rate_ticker"]

    _distributed.uninstall()

    assert _th.current_rank_span() is None
    assert not ticker._thread.is_alive()


def test_grad_comm_emits_metric_but_no_span_when_layer_one_off(monkeypatch, _setup_single_rank, tracer, test_spans):
    """Default-off path: grad_comm metric flows, no per-bucket span."""
    from ddtrace import config
    from ddtrace.contrib.internal.pytorch import _distributed

    monkeypatch.setattr(config.pytorch, "collective_trace_enabled", False)
    monkeypatch.setattr(config.pytorch, "grad_comm_enabled", True)

    fake = mock.Mock()
    _th.install_metrics_client(fake)

    class _FakeBucket:
        def buffer(self):
            return torch.zeros(64)

    def _user_hook(state, bucket):
        return None

    chained = _distributed._make_chained_comm_hook(_user_hook)
    chained(None, _FakeBucket())

    # Layer Zero metric was emitted with op=grad_comm.
    op_tags = [
        tag
        for call in fake.distribution.call_args_list
        for tag in (call.kwargs.get("tags") or (call.args[2] if len(call.args) > 2 else []))
        if tag.startswith("op:")
    ]
    assert "op:grad_comm" in op_tags

    # No Layer One span was opened.
    spans = test_spans.get_spans()
    assert not any(s.name == "pytorch.grad_comm" for s in spans)


def test_grad_comm_emits_span_and_metric_when_layer_one_on(monkeypatch, _setup_single_rank, tracer, test_spans):
    from ddtrace import config
    from ddtrace.contrib.internal.pytorch import _distributed

    monkeypatch.setattr(config.pytorch, "collective_trace_enabled", True)
    monkeypatch.setattr(config.pytorch, "grad_comm_enabled", True)

    fake = mock.Mock()
    _th.install_metrics_client(fake)

    class _FakeBucket:
        def buffer(self):
            return torch.zeros(64)

    def _user_hook(state, bucket):
        return None

    chained = _distributed._make_chained_comm_hook(_user_hook)
    chained(None, _FakeBucket())

    spans = test_spans.get_spans()
    assert any(s.name == "pytorch.grad_comm" for s in spans)
    op_tags = [
        tag
        for call in fake.distribution.call_args_list
        for tag in (call.kwargs.get("tags") or (call.args[2] if len(call.args) > 2 else []))
        if tag.startswith("op:")
    ]
    assert "op:grad_comm" in op_tags


def test_async_op_collective_still_records_metric(monkeypatch, _setup_single_rank, tracer, test_spans):
    """`async_op=True` returns a Work handle immediately. The wall-clock
    duration is short (submission cost only) but the metric should still flow.
    """
    from ddtrace import config

    monkeypatch.setattr(config.pytorch, "collective_trace_enabled", False)
    fake = mock.Mock()
    _th.install_metrics_client(fake)
    port = _free_port_for_test()
    monkeypatch.setenv("MASTER_ADDR", "127.0.0.1")
    monkeypatch.setenv("MASTER_PORT", str(port))
    dist.init_process_group(backend="gloo", rank=0, world_size=1)
    try:
        t = torch.zeros(4)
        work = dist.all_reduce(t, async_op=True)
        if work is not None:
            work.wait()
    finally:
        dist.destroy_process_group()
    # Metric should still have been emitted (with a short duration).
    duration_calls = [c for c in fake.distribution.call_args_list if c.args[0] == "collective.duration_ms"]
    assert duration_calls, "async_op=True did not emit collective.duration_ms"


def test_late_patch_starts_layer_zero_when_distributed_already_initialized(monkeypatch, tracer):
    """`patch()` after `init_process_group()` should run bootstrap and
    open the rank-root span + start the ticker.
    """
    from ddtrace.contrib.internal.pytorch import _distributed

    _th.reset_device_cache()
    _th.reset_metrics_state()
    _th.close_rank_root()
    _distributed._state.update({"bootstrapped": False, "rank": 0, "world_size": 1, "job_id": None})

    with (
        mock.patch.object(_device, "_cuda_is_available", return_value=False),
        mock.patch.object(_device, "_hostname", return_value="h-late"),
    ):
        port = _free_port_for_test()
        monkeypatch.setenv("MASTER_ADDR", "127.0.0.1")
        monkeypatch.setenv("MASTER_PORT", str(port))
        dist.init_process_group(backend="gloo", rank=0, world_size=1)
        try:
            pytorch_patch.patch()
            assert _th.current_rank_span() is not None, "rank-root span not opened by late patch"
            assert _distributed._state.get("rate_ticker") is not None, "ticker not started by late patch"
        finally:
            pytorch_patch.unpatch()
            dist.destroy_process_group()
            _th.reset_device_cache()
            _th.reset_metrics_state()
            _th.close_rank_root()


def test_ddp_init_updates_framework_tag_on_rank_root(monkeypatch, _setup_single_rank, tracer):
    """A real `DDP(model)` call after init_process_group should retag the
    open `pytorch.rank` span's `framework` from 'none' to 'ddp'.
    """
    import pytest

    port = _free_port_for_test()
    monkeypatch.setenv("MASTER_ADDR", "127.0.0.1")
    monkeypatch.setenv("MASTER_PORT", str(port))
    dist.init_process_group(backend="gloo", rank=0, world_size=1)
    try:
        # The rank-root span should already be open from bootstrap triggered by
        # dist.init_process_group above (the wrapper fires during _setup_single_rank's patch()).
        assert _th.current_rank_span() is not None
        # Initially framework is "none" — bootstrap runs before DDP wraps fire.
        assert _th.current_rank_span().get_tag("framework") == "none"

        model = torch.nn.Linear(4, 4)
        try:
            from torch.nn.parallel import DistributedDataParallel as DDP
        except Exception:
            pytest.skip("DistributedDataParallel not available in this build")
        try:
            ddp = DDP(model)
        except Exception as exc:
            pytest.skip("DDP construction failed (likely no CUDA or gloo config): %s" % exc)
        try:
            assert _th.current_rank_span().get_tag("framework") == "ddp"
        finally:
            del ddp
    finally:
        dist.destroy_process_group()
