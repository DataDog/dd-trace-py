from unittest import mock

import torch

from ddtrace.contrib.internal.pytorch._utils import _instrumentation_bypass
from ddtrace.contrib.internal.pytorch.patch import patch
from ddtrace.contrib.internal.pytorch.patch import unpatch


def test_init_process_group_triggers_bootstrap_once(monkeypatch):
    bootstrap = mock.Mock()
    monkeypatch.setattr(
        "ddtrace.contrib.internal.pytorch._distributed._bootstrap_distributed",
        bootstrap,
    )
    fake_init = mock.Mock()
    monkeypatch.setattr(torch.distributed, "init_process_group", fake_init)
    patch()
    try:
        torch.distributed.init_process_group(backend="gloo")
        torch.distributed.init_process_group(backend="gloo")  # second call
        assert bootstrap.call_count == 1
        assert fake_init.call_count == 2
    finally:
        unpatch()


def test_late_patch_runs_bootstrap_when_distributed_already_initialized(monkeypatch):
    """If patch() runs after init_process_group, we still bootstrap (resolve
    job_id, capture rank/world_size, start the CUDA event resolver).
    """
    bootstrap = mock.Mock()
    monkeypatch.setattr(
        "ddtrace.contrib.internal.pytorch._distributed._bootstrap_distributed",
        bootstrap,
    )
    monkeypatch.setattr(torch.distributed, "is_available", lambda: True)
    monkeypatch.setattr(torch.distributed, "is_initialized", lambda: True)
    monkeypatch.setattr(torch.distributed, "init_process_group", mock.Mock())
    patch()
    try:
        assert bootstrap.call_count == 1
    finally:
        unpatch()


def test_late_patch_skips_bootstrap_when_not_initialized(monkeypatch):
    bootstrap = mock.Mock()
    monkeypatch.setattr(
        "ddtrace.contrib.internal.pytorch._distributed._bootstrap_distributed",
        bootstrap,
    )
    monkeypatch.setattr(torch.distributed, "is_available", lambda: True)
    monkeypatch.setattr(torch.distributed, "is_initialized", lambda: False)
    monkeypatch.setattr(torch.distributed, "init_process_group", mock.Mock())
    patch()
    try:
        # Not yet initialized -> wrapper will run bootstrap on first init call.
        assert bootstrap.call_count == 0
    finally:
        unpatch()


def test_all_reduce_emits_span(enable_collective_trace, test_spans, monkeypatch):
    fake_all_reduce = mock.Mock()
    monkeypatch.setattr(torch.distributed, "all_reduce", fake_all_reduce)
    patch()
    try:
        tensor = mock.Mock(is_cuda=False, numel=lambda: 10, element_size=lambda: 4)
        torch.distributed.all_reduce(tensor)
        spans = test_spans.pop()
        names = [s.name for s in spans]
        assert "pytorch.allreduce" in names
        ar = next(s for s in spans if s.name == "pytorch.allreduce")
        assert ar.get_metric("bytes") == 40
        assert fake_all_reduce.called
    finally:
        unpatch()


def test_broadcast_emits_span(enable_collective_trace, test_spans, monkeypatch):
    monkeypatch.setattr(torch.distributed, "broadcast", mock.Mock())
    patch()
    try:
        tensor = mock.Mock(is_cuda=False, numel=lambda: 5, element_size=lambda: 8)
        torch.distributed.broadcast(tensor, src=0)
        names = [s.name for s in test_spans.pop()]
        assert "pytorch.broadcast" in names
    finally:
        unpatch()


def test_barrier_emits_span_without_bytes(enable_collective_trace, test_spans, monkeypatch):
    monkeypatch.setattr(torch.distributed, "barrier", mock.Mock())
    patch()
    try:
        torch.distributed.barrier()
        barriers = [s for s in test_spans.pop() if s.name == "pytorch.barrier"]
        assert len(barriers) == 1
        # No bytes metric for barrier (no tensor arg).
        assert barriers[0].get_metric("bytes") is None
    finally:
        unpatch()


def test_collective_bypass_skips_span(enable_collective_trace, test_spans, monkeypatch):
    monkeypatch.setattr(torch.distributed, "all_reduce", mock.Mock())
    patch()
    try:
        with _instrumentation_bypass():
            tensor = mock.Mock(is_cuda=False, numel=lambda: 4, element_size=lambda: 4)
            torch.distributed.all_reduce(tensor)
        spans = test_spans.pop()
        assert all(s.name != "pytorch.allreduce" for s in spans)
    finally:
        unpatch()


def test_all_gather_into_tensor_emits_span(enable_collective_trace, test_spans, monkeypatch):
    if not hasattr(torch.distributed, "all_gather_into_tensor"):
        import pytest

        pytest.skip("all_gather_into_tensor unavailable on this torch")
    monkeypatch.setattr(torch.distributed, "all_gather_into_tensor", mock.Mock())
    patch()
    try:
        out = mock.Mock(is_cuda=False, numel=lambda: 8, element_size=lambda: 4)
        inp = mock.Mock(is_cuda=False, numel=lambda: 2, element_size=lambda: 4)
        torch.distributed.all_gather_into_tensor(out, inp)
        spans = test_spans.pop()
        names = [s.name for s in spans]
        assert "pytorch.allgather_into_tensor" in names
        # bytes should come from the input tensor (index 1), i.e. 2 * 4 = 8.
        s = next(s for s in spans if s.name == "pytorch.allgather_into_tensor")
        assert s.get_metric("bytes") == 8
    finally:
        unpatch()


def test_reduce_scatter_sums_input_list_bytes(enable_collective_trace, test_spans, monkeypatch):
    """reduce_scatter must report the full input_list size, not just the
    output tensor.
    """
    monkeypatch.setattr(torch.distributed, "reduce_scatter", mock.Mock())
    patch()
    try:
        output = mock.Mock(is_cuda=False, numel=lambda: 4, element_size=lambda: 4)
        input_list = [
            mock.Mock(is_cuda=False, numel=lambda: 4, element_size=lambda: 4),
            mock.Mock(is_cuda=False, numel=lambda: 4, element_size=lambda: 4),
            mock.Mock(is_cuda=False, numel=lambda: 4, element_size=lambda: 4),
        ]
        torch.distributed.reduce_scatter(output, input_list)
        rs = next(s for s in test_spans.pop() if s.name == "pytorch.reducescatter")
        # 3 input tensors × 4 elements × 4 bytes = 48 (world-aggregated input).
        assert rs.get_metric("bytes") == 48
    finally:
        unpatch()


def test_reduce_scatter_tensor_emits_span(enable_collective_trace, test_spans, monkeypatch):
    if not hasattr(torch.distributed, "reduce_scatter_tensor"):
        import pytest

        pytest.skip("reduce_scatter_tensor unavailable on this torch")
    monkeypatch.setattr(torch.distributed, "reduce_scatter_tensor", mock.Mock())
    patch()
    try:
        out = mock.Mock(is_cuda=False, numel=lambda: 2, element_size=lambda: 4)
        inp = mock.Mock(is_cuda=False, numel=lambda: 8, element_size=lambda: 4)
        torch.distributed.reduce_scatter_tensor(out, inp)
        spans = test_spans.pop()
        names = [s.name for s in spans]
        assert "pytorch.reducescatter_tensor" in names
        # bytes from input tensor (index 1): 8 * 4 = 32.
        s = next(s for s in spans if s.name == "pytorch.reducescatter_tensor")
        assert s.get_metric("bytes") == 32
    finally:
        unpatch()


def test_fsdp_collectives_skip_cleanly_when_attribute_absent(monkeypatch):
    # Simulate older torch lacking the functional collective; install/uninstall
    # must not raise.
    if hasattr(torch.distributed, "all_gather_into_tensor"):
        monkeypatch.delattr(torch.distributed, "all_gather_into_tensor", raising=False)
    if hasattr(torch.distributed, "reduce_scatter_tensor"):
        monkeypatch.delattr(torch.distributed, "reduce_scatter_tensor", raising=False)
    patch()
    unpatch()  # round-trip without error


def test_install_skips_cleanly_when_distributed_unavailable(monkeypatch):
    # Simulate a torch built with USE_DISTRIBUTED=0: is_available() returns
    # False and collective APIs may be missing. patch()/unpatch() must not raise.
    monkeypatch.setattr(torch.distributed, "is_available", lambda: False)
    patch()
    unpatch()


def test_collective_span_carries_job_id_and_rank(enable_collective_trace, test_spans, monkeypatch):
    monkeypatch.setattr(torch.distributed, "all_reduce", mock.Mock())
    patch()
    try:
        # Simulate post-bootstrap state without actually running init_process_group.
        from ddtrace.contrib.internal.pytorch import _distributed as _d
        from ddtrace.contrib.internal.pytorch import _utils

        _d._state["job_id"] = "job-abc"
        _d._state["rank"] = 3
        _d._state["world_size"] = 8
        _utils.set_cached_job_id("job-abc")
        try:
            tensor = mock.Mock(is_cuda=False, numel=lambda: 2, element_size=lambda: 4)
            torch.distributed.all_reduce(tensor)
            ar = next(s for s in test_spans.pop() if s.name == "pytorch.allreduce")
            assert ar.get_tag("job_id") == "job-abc"
            assert ar.get_tag("training_job.id") == "job-abc"
            assert ar.get_metric("rank") == 3
            assert ar.get_metric("world_size") == 8
        finally:
            _d._state.update({"job_id": None, "rank": 0, "world_size": 1})
            _utils.set_cached_job_id(None)
    finally:
        unpatch()


def test_unpatch_resets_bootstrap_state(monkeypatch):
    bootstrap = mock.Mock()
    monkeypatch.setattr(
        "ddtrace.contrib.internal.pytorch._distributed._bootstrap_distributed",
        bootstrap,
    )
    monkeypatch.setattr(torch.distributed, "init_process_group", mock.Mock())
    patch()
    torch.distributed.init_process_group(backend="gloo")
    unpatch()
    patch()
    torch.distributed.init_process_group(backend="gloo")
    assert bootstrap.call_count == 2
    unpatch()


def test_destroy_process_group_closes_rank_root_and_invokes_original(monkeypatch):
    """`destroy_process_group` is the canonical "this rank is done with
    distributed training" signal. Wrapping it gives us a deterministic
    flush point for the `pytorch.rank` span (and its `collective.summary`
    tag) on graceful teardown, independent of atexit ordering.
    """
    import json as _json

    from ddtrace.contrib.internal.pytorch import _device
    from ddtrace.contrib.internal.pytorch import _metrics
    from ddtrace.contrib.internal.pytorch import _rank_root
    from ddtrace.contrib.internal.pytorch import _test_helpers as _th

    captured = mock.Mock()
    monkeypatch.setattr(torch.distributed, "destroy_process_group", captured)
    monkeypatch.setattr(torch.distributed, "is_available", lambda: True)
    _th.reset_device_cache()
    _th.close_rank_root()
    _th.reset_metrics_state()
    with (
        mock.patch.object(_device, "_cuda_is_available", return_value=False),
        mock.patch.object(_device, "_hostname", return_value="h-1"),
    ):
        _device.discover(local_rank=0)
    patch()
    try:
        _rank_root.open(rank=0, world_size=1, framework="ddp", training_job_id="job-X")
        rank_span = _th.current_rank_span()
        assert rank_span is not None
        # Drive a couple of collective samples so the close path sets the
        # summary tag — this is the whole reason we want a deterministic
        # flush hook.
        for v in (0.3, 0.4, 0.5):
            _metrics.record_collective(op="allreduce", duration_ms=v, bytes_count=64)
        torch.distributed.destroy_process_group()
        assert _th.current_rank_span() is None, "destroy_process_group did not close the rank-root"
        assert rank_span.finished
        summary = rank_span.get_tag("collective.summary")
        assert summary, "collective.summary tag missing on rank-root after destroy"
        assert _json.loads(summary)["allreduce"]["n"] == 3
        assert captured.called, "wrapped destroy_process_group was not delegated to"
    finally:
        unpatch()
        _th.close_rank_root()
        _th.reset_metrics_state()
        _th.reset_device_cache()


def test_destroy_process_group_passes_args_through(monkeypatch):
    """The wrapper must not swallow caller arguments. `destroy_process_group`
    accepts an optional ProcessGroup positional arg.
    """
    captured = mock.Mock()
    monkeypatch.setattr(torch.distributed, "destroy_process_group", captured)
    monkeypatch.setattr(torch.distributed, "is_available", lambda: True)
    patch()
    try:
        sentinel = object()
        torch.distributed.destroy_process_group(sentinel)
        captured.assert_called_once_with(sentinel)
    finally:
        unpatch()


def test_destroy_process_group_closes_rank_root_even_if_original_raises(monkeypatch):
    """If the underlying `destroy_process_group` raises (NCCL teardown
    failure, group already destroyed, etc.), we still want the rank-span
    flush to have happened — otherwise the user loses visibility into
    exactly the run that hit the teardown error.
    """
    from ddtrace.contrib.internal.pytorch import _device
    from ddtrace.contrib.internal.pytorch import _rank_root
    from ddtrace.contrib.internal.pytorch import _test_helpers as _th

    def boom(*a, **kw):
        raise RuntimeError("nccl teardown blew up")

    monkeypatch.setattr(torch.distributed, "destroy_process_group", boom)
    monkeypatch.setattr(torch.distributed, "is_available", lambda: True)
    _th.reset_device_cache()
    _th.close_rank_root()
    with (
        mock.patch.object(_device, "_cuda_is_available", return_value=False),
        mock.patch.object(_device, "_hostname", return_value="h-1"),
    ):
        _device.discover(local_rank=0)
    patch()
    try:
        _rank_root.open(rank=0, world_size=1, framework="ddp", training_job_id="job-X")
        rank_span = _th.current_rank_span()
        try:
            torch.distributed.destroy_process_group()
        except RuntimeError:
            pass
        assert rank_span.finished, "rank-root was not finished before the original raised"
        assert _th.current_rank_span() is None
    finally:
        unpatch()
        _th.close_rank_root()
        _th.reset_device_cache()


def test_collective_span_carries_training_job_id_tag(enable_collective_trace, test_spans, monkeypatch):
    """Collective spans must carry both `training_job.id` (canonical) and
    `job_id` (legacy) tags when a job id has been cached by the bootstrap.
    """
    monkeypatch.setattr(torch.distributed, "barrier", mock.Mock())
    patch()
    try:
        from ddtrace.contrib.internal.pytorch import _distributed as _d
        from ddtrace.contrib.internal.pytorch import _utils

        _d._state["rank"] = 0
        _d._state["world_size"] = 1
        _utils.set_cached_job_id("test-training-job-42")
        try:
            torch.distributed.barrier()
            spans = test_spans.pop()
            barrier_span = next(s for s in spans if s.name == "pytorch.barrier")
            assert barrier_span.get_tag("training_job.id") == "test-training-job-42"
            assert barrier_span.get_tag("job_id") == "test-training-job-42"
        finally:
            _d._state.update({"rank": 0, "world_size": 1})
            _utils.set_cached_job_id(None)
    finally:
        unpatch()


def test_bootstrap_caches_job_id_in_utils(monkeypatch):
    """_bootstrap_distributed must publish the resolved job_id via set_cached_job_id
    so non-distributed emitters (Layer 2/3) can tag spans without re-reading
    from _distributed._state.
    """
    from ddtrace.contrib.internal.pytorch import _utils

    monkeypatch.setattr(torch.distributed, "is_available", lambda: False)
    monkeypatch.setenv("DD_PYTORCH_JOB_ID", "bootstrap-test-id")
    _utils.set_cached_job_id(None)
    try:
        from ddtrace.contrib.internal.pytorch._distributed import _bootstrap_distributed

        _bootstrap_distributed()
        assert _utils.get_cached_job_id() == "bootstrap-test-id"
    finally:
        _utils.set_cached_job_id(None)


def test_wrapped_ddp_init_does_not_eagerly_register_comm_hook(monkeypatch):
    """After our DDP __init__ wrap runs, the user's subsequent
    register_comm_hook(state, my_hook) must NOT raise — meaning we did
    not occupy the single allowed comm-hook slot.
    """
    from ddtrace.contrib.internal.pytorch import _distributed

    register_calls = []

    class FakeDDP:
        module = object()

        def __init__(self):
            pass

        def register_comm_hook(self, state, hook):
            if register_calls:
                raise RuntimeError("comm hook already registered")
            register_calls.append((state, hook))

    instance = FakeDDP.__new__(FakeDDP)

    def original_init(*args, **kwargs):
        return None

    monkeypatch.setenv("DD_PYTORCH_GRAD_COMM", "true")
    _distributed._wrapped_ddp_init(
        wrapped=original_init,
        instance=instance,
        args=(),
        kwargs={},
    )
    assert register_calls == []
    instance.register_comm_hook(None, lambda s, b: None)
    assert len(register_calls) == 1


def test_chained_comm_hook_does_not_flush_per_bucket(monkeypatch):
    """Layer One grad_comm span finish must not synchronously flush the writer."""
    from ddtrace import config
    from ddtrace import tracer
    from ddtrace.contrib.internal.pytorch import _distributed

    flushes = []
    monkeypatch.setattr(tracer, "flush", lambda *a, **kw: flushes.append(1))
    config.pytorch.grad_comm_enabled = True
    config.pytorch.collective_trace_enabled = True

    class FakeBucket:
        def gradients(self):
            return []

    chained = _distributed._make_chained_comm_hook(lambda s, b: None)
    chained(None, FakeBucket())
    chained(None, FakeBucket())
    chained(None, FakeBucket())

    assert flushes == []


def test_backward_emits_one_flush_when_layer_one_on(monkeypatch):
    """Late flush must run when Layer One is on and a grad_comm span fired,
    even when Layer Two profiling is OFF (NB1 — supported configuration).
    """
    from ddtrace import config
    from ddtrace import tracer
    from ddtrace.contrib.internal.pytorch import _distributed

    flushes = []
    monkeypatch.setattr(tracer, "flush", lambda *a, **kw: flushes.append(1))
    monkeypatch.setenv("DD_PYTORCH_PROFILING", "false")
    config.pytorch.collective_trace_enabled = True

    class FakeTensor:
        pass

    _distributed._grad_comm_emitted_in_backward = True
    try:
        _distributed._wrapped_tensor_backward(
            wrapped=lambda *a, **kw: None,
            instance=FakeTensor(),
            args=(),
            kwargs={},
        )
        assert flushes == [1], "late flush did not run in Layer-One-only mode"
        assert _distributed._grad_comm_emitted_in_backward is False
    finally:
        _distributed._grad_comm_emitted_in_backward = False


def test_chained_comm_hook_sets_emitted_flag(monkeypatch):
    """After a chained grad_comm span finishes, the global flag must be
    set so the enclosing backward's late-flush path triggers.
    """
    from ddtrace import config
    from ddtrace import tracer
    from ddtrace.contrib.internal.pytorch import _distributed

    monkeypatch.setattr(tracer, "flush", lambda *a, **kw: None)
    config.pytorch.grad_comm_enabled = True
    config.pytorch.collective_trace_enabled = True

    class FakeBucket:
        def gradients(self):
            return []

    _distributed._grad_comm_emitted_in_backward = False
    chained = _distributed._make_chained_comm_hook(lambda s, b: None)
    chained(None, FakeBucket())
    assert _distributed._grad_comm_emitted_in_backward is True
    _distributed._grad_comm_emitted_in_backward = False  # cleanup


def test_layer2_wrappers_not_installed_when_profiling_disabled(monkeypatch):
    import torch

    from ddtrace import config
    from ddtrace.contrib.internal.pytorch.patch import patch
    from ddtrace.contrib.internal.pytorch.patch import unpatch

    monkeypatch.setenv("DD_PYTORCH_PROFILING", "false")
    monkeypatch.setenv("DD_PYTORCH_FORCE_INSTALL", "true")
    # Ensure Layer One is also off so Tensor.backward is not installed via
    # the layer_one_on branch (which installs the wrapper for flush purposes).
    monkeypatch.setattr(config.pytorch, "collective_trace_enabled", False)

    if getattr(torch, "_datadog_patch", False):
        unpatch()

    patch()
    try:
        assert not hasattr(torch.Tensor.backward, "__wrapped__")
        assert not hasattr(torch.optim.Optimizer.__init__, "__wrapped__")
    finally:
        unpatch()


def test_layer2_wrappers_installed_when_profiling_enabled(monkeypatch):
    import torch

    from ddtrace.contrib.internal.pytorch.patch import patch
    from ddtrace.contrib.internal.pytorch.patch import unpatch

    monkeypatch.setenv("DD_PYTORCH_PROFILING", "true")
    monkeypatch.setenv("DD_PYTORCH_FORCE_INSTALL", "true")

    if getattr(torch, "_datadog_patch", False):
        unpatch()

    patch()
    try:
        assert hasattr(torch.Tensor.backward, "__wrapped__")
    finally:
        unpatch()


def test_repatch_cycle_picks_up_profiling_toggle(monkeypatch):
    """patch() -> unpatch() -> patch() with DD_PYTORCH_PROFILING flipped
    between cycles must install/uninstall Layer-2 wrappers accordingly.
    """
    import torch

    from ddtrace import config
    from ddtrace.contrib.internal.pytorch import _distributed
    from ddtrace.contrib.internal.pytorch.patch import patch
    from ddtrace.contrib.internal.pytorch.patch import unpatch

    monkeypatch.setenv("DD_PYTORCH_FORCE_INSTALL", "true")
    # Ensure Layer One is off for the profiling-disabled cycle so that
    # Tensor.backward is not installed via the layer_one_on branch.
    monkeypatch.setattr(config.pytorch, "collective_trace_enabled", False)
    if getattr(torch, "_datadog_patch", False):
        unpatch()

    monkeypatch.setenv("DD_PYTORCH_PROFILING", "false")
    patch()
    assert not hasattr(torch.Tensor.backward, "__wrapped__")
    assert _distributed._layer2_installed is False
    unpatch()
    assert _distributed._installed is False
    assert _distributed._layer2_installed is False

    monkeypatch.setenv("DD_PYTORCH_PROFILING", "true")
    patch()
    try:
        assert hasattr(torch.Tensor.backward, "__wrapped__")
        assert _distributed._layer2_installed is True
    finally:
        unpatch()
        assert _distributed._installed is False
        assert _distributed._layer2_installed is False


def test_bootstrap_never_calls_broadcast(monkeypatch):
    """The bootstrap path must never invoke torch.distributed.broadcast_object_list."""
    import torch

    from ddtrace.contrib.internal.pytorch import _distributed

    monkeypatch.setattr(torch.distributed, "is_available", lambda: True)
    monkeypatch.setattr(torch.distributed, "is_initialized", lambda: True)
    monkeypatch.setattr(torch.distributed, "get_rank", lambda: 0)
    monkeypatch.setattr(torch.distributed, "get_world_size", lambda: 2)
    monkeypatch.delenv("DD_PYTORCH_JOB_ID", raising=False)
    monkeypatch.delenv("RAY_JOB_ID", raising=False)
    monkeypatch.delenv("TORCHELASTIC_RUN_ID", raising=False)
    monkeypatch.delenv("KUBEFLOW_TRAINING_JOB_ID", raising=False)
    monkeypatch.delenv("SLURM_JOB_ID", raising=False)

    broadcast_calls = []
    monkeypatch.setattr(
        torch.distributed,
        "broadcast_object_list",
        lambda *a, **k: broadcast_calls.append(1),
    )

    _distributed._state["bootstrapped"] = False
    _distributed._bootstrap_distributed()

    assert broadcast_calls == [], "bootstrap must not invoke broadcast_object_list"


def test_bootstrap_warns_and_omits_tag_when_no_env_job_id(monkeypatch, caplog):
    """When no env-chain id resolves: emit one-time WARNING AND skip
    publishing the rank-local UUID as training_job.id.
    """
    import logging

    import torch

    from ddtrace.contrib.internal.pytorch import _distributed
    from ddtrace.contrib.internal.pytorch import _utils

    monkeypatch.setattr(torch.distributed, "is_available", lambda: True)
    monkeypatch.setattr(torch.distributed, "is_initialized", lambda: True)
    monkeypatch.setattr(torch.distributed, "get_rank", lambda: 0)
    monkeypatch.setattr(torch.distributed, "get_world_size", lambda: 2)
    for var in ("DD_PYTORCH_JOB_ID", "RAY_JOB_ID", "TORCHELASTIC_RUN_ID", "KUBEFLOW_TRAINING_JOB_ID", "SLURM_JOB_ID"):
        monkeypatch.delenv(var, raising=False)

    _utils._default_job_id = None
    _utils._tls_job_id = type(_utils._tls_job_id)()
    _distributed._state["bootstrapped"] = False
    _distributed._no_env_job_id_warned = False

    with caplog.at_level(logging.WARNING, logger="ddtrace.contrib.internal.pytorch._distributed"):
        _distributed._bootstrap_distributed()

    assert any("DD_PYTORCH_JOB_ID" in r.message for r in caplog.records)
    assert _utils.get_cached_job_id() is None, "rank-local UUID leaked to the published job-id cache"


def test_bootstrap_no_warning_with_env_job_id(monkeypatch, caplog):
    import logging

    import torch

    from ddtrace.contrib.internal.pytorch import _distributed

    monkeypatch.setattr(torch.distributed, "is_available", lambda: True)
    monkeypatch.setattr(torch.distributed, "is_initialized", lambda: True)
    monkeypatch.setattr(torch.distributed, "get_rank", lambda: 0)
    monkeypatch.setattr(torch.distributed, "get_world_size", lambda: 2)
    monkeypatch.setenv("TORCHELASTIC_RUN_ID", "torchrun-abc")

    _distributed._state["bootstrapped"] = False
    _distributed._no_env_job_id_warned = False
    with caplog.at_level(logging.WARNING, logger="ddtrace.contrib.internal.pytorch._distributed"):
        _distributed._bootstrap_distributed()

    assert not any("DD_PYTORCH_JOB_ID" in r.message for r in caplog.records)
