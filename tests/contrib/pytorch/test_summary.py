"""Unit tests for the summary-mode reservoirs."""


def test_distribution_reservoir_records_and_drains_percentiles():
    from ddtrace.contrib.internal.pytorch._summary import drain_distribution
    from ddtrace.contrib.internal.pytorch._summary import push_distribution
    from ddtrace.contrib.internal.pytorch._summary import reset_all

    reset_all()
    for v in range(1, 101):  # 1..100 ms
        push_distribution("step.duration_ms", float(v))
    snap = drain_distribution("step.duration_ms")
    assert snap["count"] == 100
    assert snap["min"] == 1.0
    assert snap["max"] == 100.0
    assert 49 <= snap["p50"] <= 51
    assert 89 <= snap["p90"] <= 91
    assert 98 <= snap["p99"] <= 100
    # drain resets
    assert drain_distribution("step.duration_ms")["count"] == 0


def test_distribution_reservoir_caps_at_1024_via_reservoir_sampling():
    """When more than 1024 samples land, Algorithm R keeps a uniform sample."""
    from ddtrace.contrib.internal.pytorch._summary import drain_distribution
    from ddtrace.contrib.internal.pytorch._summary import push_distribution
    from ddtrace.contrib.internal.pytorch._summary import reset_all

    reset_all()
    for v in range(10_000):
        push_distribution("step.duration_ms", float(v))
    snap = drain_distribution("step.duration_ms")
    # Observed total is the actual count, but stored samples <= 1024
    assert snap["count"] == 10_000
    # The reservoir kept at most 1024 samples; percentiles still computable.
    assert snap["min"] >= 0.0
    assert snap["max"] < 10_000


def test_gauge_reservoir_tracks_first_last_min_max_mean():
    from ddtrace.contrib.internal.pytorch._summary import drain_gauge
    from ddtrace.contrib.internal.pytorch._summary import push_gauge
    from ddtrace.contrib.internal.pytorch._summary import reset_all

    reset_all()
    for v in [0.1, 0.2, 0.05, 0.3]:
        push_gauge("optim.learning_rate", v)
    snap = drain_gauge("optim.learning_rate")
    assert snap["count"] == 4
    assert snap["first"] == 0.1
    assert snap["last"] == 0.3
    assert snap["min"] == 0.05
    assert snap["max"] == 0.3
    assert abs(snap["mean"] - 0.1625) < 1e-9
    # drain resets
    assert drain_gauge("optim.learning_rate")["count"] == 0


def test_counter_reservoir_bumps_and_drains():
    from ddtrace.contrib.internal.pytorch._summary import bump_counter
    from ddtrace.contrib.internal.pytorch._summary import drain_counter
    from ddtrace.contrib.internal.pytorch._summary import reset_all

    reset_all()
    for _ in range(5):
        bump_counter("collective.allreduce.failures")
    assert drain_counter("collective.allreduce.failures") == 5
    assert drain_counter("collective.allreduce.failures") == 0


def test_drain_all_to_facets_emits_expected_keys():
    from ddtrace.contrib.internal.pytorch._summary import bump_counter
    from ddtrace.contrib.internal.pytorch._summary import drain_all_to_facets
    from ddtrace.contrib.internal.pytorch._summary import push_distribution
    from ddtrace.contrib.internal.pytorch._summary import push_gauge
    from ddtrace.contrib.internal.pytorch._summary import reset_all

    reset_all()
    push_distribution("step.duration_ms", 10.0)
    push_gauge("optim.learning_rate", 0.01)
    bump_counter("collective.allreduce.failures")

    facets = drain_all_to_facets()
    # Distributions emit p10/p50/p90/p99/min/max/mean/count keys
    assert "step.duration_ms.count" in facets
    assert "step.duration_ms.p50_ms" in facets
    # Gauges emit first/last/min/max/mean/count
    assert "optim.learning_rate.first" in facets
    assert "optim.learning_rate.mean" in facets
    # Counters emit a single value
    assert facets.get("collective.allreduce.failures_count") == 1


def test_fork_resets_reservoirs():
    """Module reset works for forked workers (called via fork handler)."""
    from ddtrace.contrib.internal.pytorch import _summary

    _summary.push_distribution("step.duration_ms", 5.0)
    assert _summary.drain_distribution("step.duration_ms")["count"] == 1

    # Simulate the fork handler firing.
    _summary._reset_child_state()
    assert _summary.drain_distribution("step.duration_ms")["count"] == 0


# ---------------------------------------------------------------------------
# Task 3: StepAccumulator + close_step_to_summary
# ---------------------------------------------------------------------------


def test_close_step_to_summary_records_step_duration():
    """First step has no prior end_ns; second step records duration."""
    from ddtrace.contrib.internal.pytorch import _summary

    _summary.reset_all()
    _summary.reset_step_accumulator()

    acc = _summary.get_step_accumulator()
    acc.forward_total_ms = 5.0
    acc.backward_total_ms = 7.0
    acc.optim_step_ms = 2.0
    _summary.close_step_to_summary(prev_step_end_ns=0, now_ns_val=1_000_000_000)

    # No step_duration recorded on first step (prev_step_end_ns == 0).
    assert _summary.drain_distribution("step.duration_ms")["count"] == 0

    acc = _summary.get_step_accumulator()
    acc.forward_total_ms = 6.0
    acc.backward_total_ms = 8.0
    acc.optim_step_ms = 2.5
    _summary.close_step_to_summary(prev_step_end_ns=1_000_000_000, now_ns_val=1_050_000_000)

    snap = _summary.drain_distribution("step.duration_ms")
    assert snap["count"] == 1
    assert abs(snap["mean"] - 50.0) < 0.01

    assert _summary.drain_distribution("step.forward_ms")["mean"] == 6.0
    assert _summary.drain_distribution("step.backward_ms")["mean"] == 8.0
    assert _summary.drain_distribution("step.optim_step_ms")["mean"] == 2.5


def test_close_step_to_summary_resets_accumulator():
    from ddtrace.contrib.internal.pytorch import _summary

    _summary.reset_step_accumulator()
    acc = _summary.get_step_accumulator()
    acc.forward_total_ms = 99.0
    _summary.close_step_to_summary(prev_step_end_ns=1_000_000_000, now_ns_val=2_000_000_000)
    acc2 = _summary.get_step_accumulator()
    assert acc2.forward_total_ms == 0.0


def test_close_step_to_summary_skips_zero_components():
    """Only non-zero accumulator fields are pushed to reservoirs."""
    from ddtrace.contrib.internal.pytorch import _summary

    _summary.reset_all()
    _summary.reset_step_accumulator()

    # Leave all component fields at 0 (only step_duration should be recorded).
    _summary.close_step_to_summary(prev_step_end_ns=1_000_000_000, now_ns_val=1_100_000_000)

    assert _summary.drain_distribution("step.duration_ms")["count"] == 1
    assert _summary.drain_distribution("step.forward_ms")["count"] == 0
    assert _summary.drain_distribution("step.backward_ms")["count"] == 0
    assert _summary.drain_distribution("step.optim_step_ms")["count"] == 0
    assert _summary.drain_distribution("step.data_fetch_ms")["count"] == 0
    assert _summary.drain_distribution("step.grad_clip_ms")["count"] == 0


def test_step_accumulator_get_returns_same_thread_object():
    from ddtrace.contrib.internal.pytorch import _summary

    _summary.reset_step_accumulator()
    acc1 = _summary.get_step_accumulator()
    acc2 = _summary.get_step_accumulator()
    assert acc1 is acc2


def test_reset_step_accumulator_creates_fresh_object():
    from ddtrace.contrib.internal.pytorch import _summary

    _summary.reset_step_accumulator()
    acc1 = _summary.get_step_accumulator()
    acc1.forward_total_ms = 42.0
    _summary.reset_step_accumulator()
    acc2 = _summary.get_step_accumulator()
    assert acc2.forward_total_ms == 0.0
    assert acc1 is not acc2


# ---------------------------------------------------------------------------
# Task 4: LR gauge + optim_step_ms distribution from optimizer_step
# ---------------------------------------------------------------------------


def test_optimizer_step_feeds_lr_and_duration_in_summary_mode(monkeypatch):
    """With verbose Layer 2 disabled, the summary feeds capture LR and
    record optim_step_ms via the step boundary's close_step_to_summary.

    NOTE: ``optimizer_step(wrapped, instance, args, kwargs)`` follows the
    wrapt convention — ``wrapped`` is the original method already bound to
    ``instance``, so we call it as ``wrapped(*args, **kwargs)`` without
    passing ``instance`` again.  The test therefore passes a zero-argument
    callable for ``wrapped``.

    NOTE on optim_step_ms: ``optimizer_step`` sets ``acc.optim_step_ms``
    so that ``close_step_to_summary`` (called at the step boundary via
    ``_maybe_close_step``) pushes it into the distribution.  We simulate
    a second step (with a non-zero prior end timestamp) so the distribution
    push actually fires.
    """
    monkeypatch.delenv("DD_PYTORCH_PROFILING", raising=False)
    import time as _time

    from ddtrace.contrib.internal.pytorch import _hooks
    from ddtrace.contrib.internal.pytorch import _summary
    from ddtrace.contrib.internal.pytorch._utils import set_last_optimizer_step_end_ns

    _summary.reset_all()
    _summary.reset_step_accumulator()
    # Reset designation so this FakeOptimizer gets designated fresh.
    _hooks._designated_optimizer_ref = None
    _hooks._designated_warning_emitted = False
    _hooks._step_counter = 0
    # Reset the last-step-end timestamp so close_step_to_summary treats
    # the first call as the first step (no prior end → no push).
    set_last_optimizer_step_end_ns(0)

    class FakeOptimizer:
        param_groups = [{"lr": 0.01}]

    opt = FakeOptimizer()

    # Simulate the wrapt-bound callable: no self argument.
    def fake_step():
        return None

    # First call: designates opt and sets the step-end timestamp.
    # close_step_to_summary skips distribution push (prev_end == 0).
    _hooks.optimizer_step(fake_step, opt, (), {})

    # Second call: prev_end is now set, so close_step_to_summary will push
    # step.duration_ms + step.optim_step_ms (from accumulator).
    _time.sleep(0.001)
    _hooks.optimizer_step(fake_step, opt, (), {})

    lr_snap = _summary.drain_gauge("optim.learning_rate")
    assert lr_snap["count"] == 2  # both calls pushed LR
    assert lr_snap["last"] == 0.01

    # optim_step_ms is pushed to the distribution by close_step_to_summary
    # when the second step closes.
    snap = _summary.drain_distribution("step.optim_step_ms")
    assert snap["count"] == 1
    assert snap["mean"] >= 0.0


def test_optimizer_step_captures_lr_even_when_step_raises(monkeypatch):
    """LR is captured BEFORE the wrapped call; an exception in optimizer.step
    must not skip the LR feed.

    NOTE: same wrapt-bound convention as the test above — ``wrapped`` takes
    no ``self`` argument.
    """
    monkeypatch.delenv("DD_PYTORCH_PROFILING", raising=False)
    from ddtrace.contrib.internal.pytorch import _hooks
    from ddtrace.contrib.internal.pytorch import _summary

    _summary.reset_all()
    _summary.reset_step_accumulator()

    class FakeOptimizer:
        param_groups = [{"lr": 0.005}]

    def bad_step():
        raise RuntimeError("boom")

    opt = FakeOptimizer()
    try:
        _hooks.optimizer_step(bad_step, opt, (), {})
    except RuntimeError:
        pass

    lr_snap = _summary.drain_gauge("optim.learning_rate")
    assert lr_snap["count"] == 1
    assert lr_snap["last"] == 0.005


def test_maybe_close_step_feeds_summary_without_layer2(monkeypatch):
    """Even when DD_PYTORCH_PROFILING is off, _maybe_close_step still
    feeds the summary path once the optimizer is designated.
    """
    import time as _t

    monkeypatch.delenv("DD_PYTORCH_PROFILING", raising=False)
    from ddtrace.contrib.internal.pytorch import _hooks
    from ddtrace.contrib.internal.pytorch import _summary

    _summary.reset_all()
    _summary.reset_step_accumulator()

    # Reset designation state so a fresh instance can be designated.
    _hooks._designated_optimizer_ref = None
    _hooks._designated_warning_emitted = False
    _hooks._step_counter = 0

    # Reset step-end timestamp so the first _maybe_close_step sees prev_end==0.
    from ddtrace.contrib.internal.pytorch._utils import set_last_optimizer_step_end_ns

    set_last_optimizer_step_end_ns(0)

    class FakeOptimizer:
        pass

    inst = FakeOptimizer()
    # Designate the instance explicitly (normally done via optimizer_step).
    _hooks._maybe_designate(inst)
    assert _hooks._is_designated(inst), "instance must be designated before testing"

    # First call: no previous end_ns, so step_duration is not recorded.
    _hooks._maybe_close_step(inst)

    # Second call: should record step duration.
    _t.sleep(0.005)
    _hooks._maybe_close_step(inst)

    snap = _summary.drain_distribution("step.duration_ms")
    assert snap["count"] == 1
    assert snap["mean"] >= 4.0  # at least ~5 ms sleep


# ---------------------------------------------------------------------------
# Task 5: Tensor.backward feed (loss + backward_ms)
# ---------------------------------------------------------------------------


def test_tensor_backward_summary_feeds_loss_when_scalar(monkeypatch):
    """When loss is a 0-d tensor and DD_PYTORCH_CAPTURE_LOSS=true (default),
    `train.loss` reservoir receives the loss value.
    """
    monkeypatch.delenv("DD_PYTORCH_PROFILING", raising=False)
    monkeypatch.delenv("DD_PYTORCH_CAPTURE_LOSS", raising=False)  # default true
    from ddtrace.contrib.internal.pytorch import _distributed
    from ddtrace.contrib.internal.pytorch import _summary

    _summary.reset_all()

    class FakeLossTensor:
        def numel(self):
            return 1

        def item(self):
            return 0.42

    called = {}

    def fake_backward(*args, **kwargs):
        called["yes"] = True

    _distributed._wrapped_tensor_backward(
        wrapped=fake_backward,
        instance=FakeLossTensor(),
        args=(),
        kwargs={},
    )
    assert called.get("yes")

    loss_snap = _summary.drain_distribution("train.loss")
    assert loss_snap["count"] == 1
    assert abs(loss_snap["mean"] - 0.42) < 1e-6


def test_tensor_backward_summary_skips_loss_when_capture_disabled(monkeypatch):
    monkeypatch.setenv("DD_PYTORCH_CAPTURE_LOSS", "false")
    from ddtrace.contrib.internal.pytorch import _distributed
    from ddtrace.contrib.internal.pytorch import _summary

    _summary.reset_all()

    class FakeLossTensor:
        def numel(self):
            return 1

        def item(self):
            return 0.99

    _distributed._wrapped_tensor_backward(
        wrapped=lambda *a, **kw: None,
        instance=FakeLossTensor(),
        args=(),
        kwargs={},
    )

    assert _summary.drain_distribution("train.loss")["count"] == 0


def test_tensor_backward_summary_skips_loss_when_not_scalar(monkeypatch):
    """If loss isn't 0-d, skip silently."""
    monkeypatch.delenv("DD_PYTORCH_CAPTURE_LOSS", raising=False)
    from ddtrace.contrib.internal.pytorch import _distributed
    from ddtrace.contrib.internal.pytorch import _summary

    _summary.reset_all()

    class FakeLossTensor:
        def numel(self):
            return 5  # vector loss

        def item(self):
            raise RuntimeError("can only convert scalar tensor")

    _distributed._wrapped_tensor_backward(
        wrapped=lambda *a, **kw: None,
        instance=FakeLossTensor(),
        args=(),
        kwargs={},
    )

    assert _summary.drain_distribution("train.loss")["count"] == 0


def test_tensor_backward_summary_feeds_backward_ms(monkeypatch):
    """backward_ms reservoir is fed regardless of profiling state."""
    monkeypatch.delenv("DD_PYTORCH_PROFILING", raising=False)
    monkeypatch.delenv("DD_PYTORCH_CAPTURE_LOSS", raising=False)
    from ddtrace.contrib.internal.pytorch import _distributed
    from ddtrace.contrib.internal.pytorch import _summary

    _summary.reset_all()
    _summary.reset_step_accumulator()

    class FakeLossTensor:
        def numel(self):
            return 1

        def item(self):
            return 0.0

    def slow_backward(*args, **kwargs):
        import time as _t

        _t.sleep(0.005)

    _distributed._wrapped_tensor_backward(
        wrapped=slow_backward,
        instance=FakeLossTensor(),
        args=(),
        kwargs={},
    )

    snap = _summary.drain_distribution("step.backward_ms")
    assert snap["count"] == 1
    assert snap["mean"] >= 4.0
    acc = _summary.get_step_accumulator()
    assert acc.backward_total_ms >= 4.0


# ---------------------------------------------------------------------------
# Task 6: forward + data_fetch accumulation from forward hooks
# ---------------------------------------------------------------------------


def test_forward_hooks_feed_step_forward_ms(monkeypatch):
    """`_forward_pre_hook` + `_forward_hook` populate step.forward_ms."""
    monkeypatch.delenv("DD_PYTORCH_PROFILING", raising=False)
    import time as _t

    from ddtrace.contrib.internal.pytorch import _hooks
    from ddtrace.contrib.internal.pytorch import _summary

    _summary.reset_all()
    _summary.reset_step_accumulator()
    # Clear summary stack from prior tests in this thread.
    if hasattr(_hooks._FORWARD_TLS, "summary_stack"):
        _hooks._FORWARD_TLS.summary_stack = []

    _hooks._forward_pre_hook(module=None, inputs=())
    _t.sleep(0.005)
    _hooks._forward_hook(module=None, inputs=(), output=None)

    snap = _summary.drain_distribution("step.forward_ms")
    assert snap["count"] == 1
    assert snap["mean"] >= 4.0
    acc = _summary.get_step_accumulator()
    assert acc.forward_total_ms >= 4.0


def test_forward_pre_hook_feeds_data_fetch_after_optimizer_step(monkeypatch):
    """The gap between last optimizer.step end and the next forward start
    is captured as step.data_fetch_ms.
    """
    monkeypatch.delenv("DD_PYTORCH_PROFILING", raising=False)
    import time as _t

    from ddtrace.contrib.internal.pytorch import _hooks
    from ddtrace.contrib.internal.pytorch import _summary
    from ddtrace.contrib.internal.pytorch._utils import set_last_optimizer_step_end_ns

    _summary.reset_all()
    _summary.reset_step_accumulator()
    if hasattr(_hooks._FORWARD_TLS, "summary_stack"):
        _hooks._FORWARD_TLS.summary_stack = []
    _hooks._DATA_LOAD_TLS.emitted = False

    set_last_optimizer_step_end_ns(_t.perf_counter_ns())
    _t.sleep(0.003)
    _hooks._forward_pre_hook(module=None, inputs=())

    snap = _summary.drain_distribution("step.data_fetch_ms")
    assert snap["count"] == 1
    assert snap["mean"] >= 2.0


def test_data_fetch_recorded_once_per_step(monkeypatch):
    """Subsequent forwards in the same step do NOT record data_fetch_ms
    again (only the FIRST forward of a step counts).
    """
    monkeypatch.delenv("DD_PYTORCH_PROFILING", raising=False)
    import time as _t

    from ddtrace.contrib.internal.pytorch import _hooks
    from ddtrace.contrib.internal.pytorch import _summary
    from ddtrace.contrib.internal.pytorch._utils import set_last_optimizer_step_end_ns

    _summary.reset_all()
    _summary.reset_step_accumulator()
    if hasattr(_hooks._FORWARD_TLS, "summary_stack"):
        _hooks._FORWARD_TLS.summary_stack = []
    _hooks._DATA_LOAD_TLS.emitted = False

    set_last_optimizer_step_end_ns(_t.perf_counter_ns())
    _hooks._forward_pre_hook(module=None, inputs=())
    _hooks._forward_hook(module=None, inputs=(), output=None)
    _hooks._forward_pre_hook(module=None, inputs=())  # second forward in same step
    _hooks._forward_hook(module=None, inputs=(), output=None)

    snap = _summary.drain_distribution("step.data_fetch_ms")
    assert snap["count"] == 1  # only the first forward recorded it


# ---------------------------------------------------------------------------
# Task 7: clip_grad_norm_ wrap (grad_norm + grad_clip_ms)
# ---------------------------------------------------------------------------


def test_clip_grad_norm_wrap_feeds_grad_norm_and_grad_clip_ms():
    """When clip_grad_norm_ is wrapped and called with a tensor return,
    `train.grad_norm` receives the norm value and `step.grad_clip_ms`
    receives the call duration.
    """
    import time as _t

    from ddtrace.contrib.internal.pytorch import _distributed
    from ddtrace.contrib.internal.pytorch import _summary

    _summary.reset_all()
    _summary.reset_step_accumulator()

    class FakeNorm:
        def item(self):
            return 1.42

    def fake_clip(*args, **kwargs):
        _t.sleep(0.005)
        return FakeNorm()

    _distributed._wrapped_clip_grad_norm(wrapped=fake_clip, instance=None, args=(), kwargs={})

    gn = _summary.drain_distribution("train.grad_norm")
    assert gn["count"] == 1
    assert abs(gn["mean"] - 1.42) < 1e-6

    gc = _summary.drain_distribution("step.grad_clip_ms")
    assert gc["count"] == 1
    assert gc["mean"] >= 4.0


def test_clip_grad_norm_wrap_handles_float_return():
    """Some clip_grad_norm_ versions return a plain float, not a tensor."""
    from ddtrace.contrib.internal.pytorch import _distributed
    from ddtrace.contrib.internal.pytorch import _summary

    _summary.reset_all()

    def fake_clip(*args, **kwargs):
        return 0.5

    _distributed._wrapped_clip_grad_norm(wrapped=fake_clip, instance=None, args=(), kwargs={})

    gn = _summary.drain_distribution("train.grad_norm")
    assert gn["count"] == 1
    assert abs(gn["mean"] - 0.5) < 1e-6


def test_clip_grad_norm_wrap_timing_recorded_even_on_exception():
    """If clip_grad_norm_ raises, grad_clip_ms is still recorded; the
    exception propagates.
    """
    from ddtrace.contrib.internal.pytorch import _distributed
    from ddtrace.contrib.internal.pytorch import _summary

    _summary.reset_all()

    def bad_clip(*args, **kwargs):
        raise RuntimeError("clip failed")

    try:
        _distributed._wrapped_clip_grad_norm(wrapped=bad_clip, instance=None, args=(), kwargs={})
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected RuntimeError to propagate")

    gc = _summary.drain_distribution("step.grad_clip_ms")
    assert gc["count"] == 1
    # grad_norm not recorded because the function raised.
    assert _summary.drain_distribution("train.grad_norm")["count"] == 0


def test_install_grad_clip_idempotent(monkeypatch):
    """install_grad_clip + uninstall_grad_clip can be called multiple
    times safely.
    """
    import torch

    monkeypatch.setenv("DD_PYTORCH_FORCE_INSTALL", "true")
    monkeypatch.setenv("DD_PYTORCH_COLLECTIVE_TRACE", "true")
    from ddtrace.contrib.internal.pytorch.patch import patch
    from ddtrace.contrib.internal.pytorch.patch import unpatch

    if getattr(torch, "_datadog_patch", False):
        unpatch()

    patch()
    try:
        assert hasattr(torch.nn.utils.clip_grad_norm_, "__wrapped__")
    finally:
        unpatch()

    # Calling patch / unpatch twice must not stack wraps.
    patch()
    unpatch()


# ---------------------------------------------------------------------------
# Task 8: GPU memory sampling at step close
# ---------------------------------------------------------------------------


def test_sample_memory_feeds_gauge_when_cuda_available(monkeypatch):
    """Both current and peak memory are sampled into gauge reservoirs."""
    import torch

    from ddtrace.contrib.internal.pytorch import _summary

    _summary.reset_all()
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(torch.cuda, "memory_allocated", lambda: 1024)
    monkeypatch.setattr(torch.cuda, "max_memory_allocated", lambda: 2048)

    _summary.sample_memory_at_step_close()

    used = _summary.drain_gauge("memory.gpu_used_memory_bytes")
    assert used["count"] == 1
    assert used["last"] == 1024

    peak = _summary.drain_gauge("memory.peak_gpu_used_memory_bytes")
    assert peak["count"] == 1
    assert peak["last"] == 2048


def test_sample_memory_no_op_when_cuda_unavailable(monkeypatch):
    import torch

    from ddtrace.contrib.internal.pytorch import _summary

    _summary.reset_all()
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    _summary.sample_memory_at_step_close()

    assert _summary.drain_gauge("memory.gpu_used_memory_bytes")["count"] == 0
    assert _summary.drain_gauge("memory.peak_gpu_used_memory_bytes")["count"] == 0


def test_close_step_to_summary_samples_memory(monkeypatch):
    """End-to-end: closing a step in summary mode samples memory."""
    import torch

    from ddtrace.contrib.internal.pytorch import _summary

    _summary.reset_all()
    _summary.reset_step_accumulator()
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(torch.cuda, "memory_allocated", lambda: 512)
    monkeypatch.setattr(torch.cuda, "max_memory_allocated", lambda: 1024)

    # Close one step.
    _summary.close_step_to_summary(prev_step_end_ns=1_000_000_000, now_ns_val=2_000_000_000)

    # Memory feed fires regardless of whether step.duration_ms was recorded.
    used = _summary.drain_gauge("memory.gpu_used_memory_bytes")
    assert used["count"] == 1
    assert used["last"] == 512


# ---------------------------------------------------------------------------
# Task 9: model fingerprinting + analytical MFU/Tflops
# ---------------------------------------------------------------------------


def test_fingerprint_model_counts_params_and_detects_transformer():
    import torch

    from ddtrace.contrib.internal.pytorch import _summary

    _summary._model_fingerprinted = False  # force re-fingerprint

    class FakeBlock(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.self_attn = torch.nn.Linear(8, 8)
            self.mlp = torch.nn.Linear(8, 8)

        def forward(self, x):
            return self.mlp(self.self_attn(x))

    class FakeModel(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.blocks = torch.nn.ModuleList([FakeBlock() for _ in range(4)])

        def forward(self, x):
            for b in self.blocks:
                x = b(x)
            return x

    _summary._fingerprint_model(FakeModel())

    assert _summary._model_fingerprinted is True
    assert _summary._model_param_count > 0
    assert _summary._model_is_transformer is True
    assert _summary._model_layers == 4


def test_fingerprint_model_idempotent():
    import torch

    from ddtrace.contrib.internal.pytorch import _summary

    _summary._model_fingerprinted = False
    m = torch.nn.Linear(4, 4)
    _summary._fingerprint_model(m)
    first_count = _summary._model_param_count

    # A second call doesn't change state.
    other = torch.nn.Linear(100, 100)
    _summary._fingerprint_model(other)
    assert _summary._model_param_count == first_count


def test_record_embedding_input_accumulates_tokens():
    from ddtrace.contrib.internal.pytorch import _summary

    _summary.reset_step_accumulator()
    _summary.record_embedding_input((4, 128))  # batch=4, seq=128
    acc = _summary.get_step_accumulator()
    assert acc.tokens_this_step == 512

    _summary.record_embedding_input((2, 64))
    assert _summary.get_step_accumulator().tokens_this_step == 512 + 128


def test_mfu_computed_for_transformer_with_tokens(monkeypatch):
    from ddtrace.contrib.internal.pytorch import _device
    from ddtrace.contrib.internal.pytorch import _summary

    _summary.reset_all()
    _summary.reset_step_accumulator()

    # Pretend we fingerprinted a 7B-param transformer.
    _summary._model_fingerprinted = True
    _summary._model_param_count = 7_000_000_000
    _summary._model_is_transformer = True
    _summary._model_layers = 32
    _summary._model_dtype = "bfloat16"

    # Stub device info to A100.
    class FakeDeviceInfo:
        gpu_name = "NVIDIA A100-SXM4-80GB"

    monkeypatch.setattr(_device, "get", lambda: FakeDeviceInfo())

    acc = _summary.get_step_accumulator()
    acc.tokens_this_step = 2048

    # 200 ms step
    _summary.close_step_to_summary(prev_step_end_ns=1_000_000_000, now_ns_val=1_200_000_000)

    tflops = _summary.drain_distribution("train.tflops")
    mfu = _summary.drain_distribution("train.mfu")
    assert tflops["count"] == 1
    assert mfu["count"] == 1
    # 6 * 7e9 * 2048 / 0.2 = 4.3008e14 → 430.08 TFLOPS
    assert abs(tflops["mean"] - 430.08) < 1.0
    # A100 BF16 peak = 312 TFLOPS → MFU ≈ 1.378
    assert abs(mfu["mean"] - 1.378) < 0.01


def test_mfu_skipped_for_non_transformer(monkeypatch):
    from ddtrace.contrib.internal.pytorch import _summary

    _summary.reset_all()
    _summary.reset_step_accumulator()
    _summary._model_fingerprinted = True
    _summary._model_param_count = 1_000_000
    _summary._model_is_transformer = False  # NOT transformer

    acc = _summary.get_step_accumulator()
    acc.tokens_this_step = 100

    _summary.close_step_to_summary(prev_step_end_ns=1_000_000_000, now_ns_val=1_100_000_000)

    assert _summary.drain_distribution("train.mfu")["count"] == 0
    assert _summary.drain_distribution("train.tflops")["count"] == 0


def test_mfu_disabled_via_env(monkeypatch):
    from ddtrace.contrib.internal.pytorch import _summary

    monkeypatch.setenv("DD_PYTORCH_MFU_ENABLED", "false")

    _summary.reset_all()
    _summary.reset_step_accumulator()
    _summary._model_fingerprinted = True
    _summary._model_param_count = 7_000_000_000
    _summary._model_is_transformer = True
    _summary._model_dtype = "bfloat16"

    acc = _summary.get_step_accumulator()
    acc.tokens_this_step = 2048

    _summary.close_step_to_summary(prev_step_end_ns=1_000_000_000, now_ns_val=1_200_000_000)

    assert _summary.drain_distribution("train.mfu")["count"] == 0


def test_lookup_peak_flops_matches_known_gpus():
    from ddtrace.contrib.internal.pytorch._device import lookup_peak_flops

    assert lookup_peak_flops("NVIDIA A100-SXM4-80GB", "bfloat16") == 312e12
    assert lookup_peak_flops("NVIDIA H100 PCIe", "bfloat16") == 989e12
    assert lookup_peak_flops("AMD MI300X", "bfloat16") == 1300e12
    assert lookup_peak_flops("Unknown GPU", "bfloat16") is None
    assert lookup_peak_flops(None, "bfloat16") is None


# ---------------------------------------------------------------------------
# Task 10: MoE module detection + dropped-token counter
# ---------------------------------------------------------------------------


def test_detect_moe_modules_finds_deepspeed_moe_class():
    """Class-name duck-typing detects DeepSpeed-MoE layers."""
    import torch

    from ddtrace.contrib.internal.pytorch import _summary

    _summary._moe_modules_cached = None  # force re-detection

    class MoE(torch.nn.Module):
        """Pretends to be deepspeed.moe.layer.MoE."""

        def forward(self, x):
            return x

    class Container(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.moe1 = MoE()
            self.moe2 = MoE()
            self.linear = torch.nn.Linear(4, 4)

    found = _summary._detect_moe_modules(Container())
    assert len(found) == 2


def test_detect_moe_modules_idempotent():
    import torch

    from ddtrace.contrib.internal.pytorch import _summary

    _summary._moe_modules_cached = None

    class MoE(torch.nn.Module):
        def forward(self, x):
            return x

    _summary._detect_moe_modules(MoE())
    first = _summary._moe_modules_cached
    # Second call with a different model is a no-op.
    _summary._detect_moe_modules(torch.nn.Linear(4, 4))
    assert _summary._moe_modules_cached is first


def test_read_moe_drop_ratio_none_when_no_modules():
    from ddtrace.contrib.internal.pytorch import _summary

    _summary._moe_modules_cached = []
    assert _summary.read_moe_drop_ratio() is None


def test_read_moe_drop_ratio_computes_dropped_fraction():
    """100 tokens went in, 80 reached experts → 20% dropped."""
    from ddtrace.contrib.internal.pytorch import _summary

    class FakeMoE:
        # DeepSpeed-MoE style: list-like expert counts, scalar input count.
        exp_counts = [50, 30]  # 80 tokens accepted
        input_token_count = 100

    _summary._moe_modules_cached = [FakeMoE()]
    ratio = _summary.read_moe_drop_ratio()
    assert ratio is not None
    assert abs(ratio - 0.2) < 1e-6


def test_read_moe_drop_ratio_with_tensor_counts(monkeypatch):
    """Counters can be torch tensors; the helper sums them via .sum().item()."""
    import torch

    from ddtrace.contrib.internal.pytorch import _summary

    class FakeMoE:
        # Megatron / fairscale style: tensor counts
        tokens_per_expert = torch.tensor([40, 30, 20])  # 90 routed
        n_tokens = 100

    _summary._moe_modules_cached = [FakeMoE()]
    ratio = _summary.read_moe_drop_ratio()
    assert ratio is not None
    assert abs(ratio - 0.1) < 1e-6


def test_read_moe_drop_ratio_returns_none_when_no_total():
    """If input_token_count is 0 or missing, ratio is None."""
    from ddtrace.contrib.internal.pytorch import _summary

    class FakeMoE:
        exp_counts = [10, 5]
        # No total attr available.

    _summary._moe_modules_cached = [FakeMoE()]
    assert _summary.read_moe_drop_ratio() is None


def test_close_step_to_summary_feeds_dropped_tokens(monkeypatch):
    """End-to-end: close a step in summary mode with MoE modules present;
    `train.avg_dropped_tokens` reservoir receives the ratio.
    """
    from ddtrace.contrib.internal.pytorch import _summary

    class FakeMoE:
        exp_counts = [80]  # 80 routed
        input_token_count = 100  # 100 input

    _summary.reset_all()
    _summary.reset_step_accumulator()
    _summary._moe_modules_cached = [FakeMoE()]

    _summary.close_step_to_summary(prev_step_end_ns=1_000_000_000, now_ns_val=1_100_000_000)

    snap = _summary.drain_distribution("train.avg_dropped_tokens")
    assert snap["count"] == 1
    assert abs(snap["mean"] - 0.2) < 1e-6


# ---------------------------------------------------------------------------
# Task 11: sampled CUDA-event collective GPU timing into summary reservoirs
# ---------------------------------------------------------------------------


def test_resolver_summary_marker_pushes_gpu_duration(monkeypatch):
    """The resolver's summary marker path pushes elapsed time to the
    ``collective.<op>.gpu_duration_ms`` reservoir.
    """
    import time as _t

    from ddtrace.contrib.internal.pytorch import _distributed
    from ddtrace.contrib.internal.pytorch import _summary

    _summary.reset_all()

    class FakeEvent:
        def __init__(self, t):
            self._t = t

        def query(self):
            return True

        def record(self):
            pass

        def elapsed_time(self, other):
            return float(other._t - self._t)

    resolver = _distributed.CudaEventResolver(poll_interval=0.005, capacity=64)
    resolver.start()
    try:
        resolver.submit_for_summary("allreduce", FakeEvent(0), FakeEvent(42))
        # Give the background thread time to drain.
        deadline = _t.monotonic() + 2.0
        while _t.monotonic() < deadline:
            snap = _summary.drain_distribution("collective.allreduce.gpu_duration_ms")
            if snap["count"] > 0:
                assert abs(snap["mean"] - 42.0) < 0.1
                return
            _t.sleep(0.01)
        raise AssertionError("resolver did not drain summary marker within timeout")
    finally:
        resolver.stop(timeout=2.0)


def test_resolver_summary_marker_dropped_silently_on_query_error():
    """If end.query() raises, the marker is dropped without emitting to
    the summary reservoir and without raising in the background thread.
    """
    import time as _t

    from ddtrace.contrib.internal.pytorch import _distributed
    from ddtrace.contrib.internal.pytorch import _summary

    _summary.reset_all()

    class BadEvent:
        def query(self):
            raise RuntimeError("nope")

        def elapsed_time(self, other):
            return 0.0

    resolver = _distributed.CudaEventResolver(poll_interval=0.005, capacity=64)
    resolver.start()
    try:
        resolver.submit_for_summary("allreduce", BadEvent(), BadEvent())
        # Wait enough for the background thread to attempt and discard.
        _t.sleep(0.05)
        snap = _summary.drain_distribution("collective.allreduce.gpu_duration_ms")
        assert snap["count"] == 0
    finally:
        resolver.stop(timeout=2.0)


def test_resolver_summary_marker_overflow_silent():
    """When the queue overflows on a summary marker, the evicted entry is
    dropped silently (no span.finish / set_tag calls to make).
    """
    import time as _t

    from ddtrace.contrib.internal.pytorch import _distributed
    from ddtrace.contrib.internal.pytorch import _summary

    _summary.reset_all()

    class NeverReadyEvent:
        def query(self):
            return False

        def elapsed_time(self, other):
            return 0.0

    # capacity=1 forces an eviction on the second submit.
    resolver = _distributed.CudaEventResolver(poll_interval=60.0, capacity=1)
    resolver.start()
    try:
        resolver.submit_for_summary("allreduce", NeverReadyEvent(), NeverReadyEvent())
        # Second submit evicts the first silently.
        resolver.submit_for_summary("allreduce", NeverReadyEvent(), NeverReadyEvent())
        _t.sleep(0.02)
        # No crash; reservoir is empty because nothing resolved.
        snap = _summary.drain_distribution("collective.allreduce.gpu_duration_ms")
        assert snap["count"] == 0
    finally:
        resolver.stop(timeout=2.0)


def test_resolver_flush_remaining_drops_markers_silently():
    """stop() calls _flush_remaining, which drops summary markers without
    calling span.finish() (there is no span).
    """
    from ddtrace.contrib.internal.pytorch import _distributed
    from ddtrace.contrib.internal.pytorch import _summary

    _summary.reset_all()

    class NeverReadyEvent:
        def query(self):
            return False

        def elapsed_time(self, other):
            return 0.0

    # Use a long poll interval so the entry is still queued at stop time.
    resolver = _distributed.CudaEventResolver(poll_interval=60.0, capacity=64)
    resolver.start()
    resolver.submit_for_summary("broadcast", NeverReadyEvent(), NeverReadyEvent())
    # stop() with short timeout — the thread exits, _flush_remaining fires.
    resolver.stop(timeout=0.1)
    # No exception = marker was dropped, not finished as a span.
    snap = _summary.drain_distribution("collective.broadcast.gpu_duration_ms")
    assert snap["count"] == 0


def test_summary_marker_does_not_break_span_path():
    """Mixing summary markers and real span entries in the queue does not
    corrupt the span path: spans are still finished with gpu.duration_ms.
    """
    import time as _t
    from unittest import mock

    from ddtrace.contrib.internal.pytorch import _distributed
    from ddtrace.contrib.internal.pytorch import _summary

    _summary.reset_all()

    class ImmediateEvent:
        def __init__(self, ms=10.0):
            self._ms = ms

        def query(self):
            return True

        def elapsed_time(self, other):
            return self._ms

    span = mock.MagicMock()
    span.finished = False

    def _finish():
        span.finished = True

    span.finish.side_effect = _finish

    resolver = _distributed.CudaEventResolver(poll_interval=0.005, capacity=64)
    resolver.start()
    try:
        # Interleave: summary marker, then real span.
        resolver.submit_for_summary("allgather", ImmediateEvent(5.0), ImmediateEvent(0.0))
        resolver.submit(span, ImmediateEvent(0.0), ImmediateEvent(10.0))

        deadline = _t.monotonic() + 2.0
        while _t.monotonic() < deadline and not span.finished:
            _t.sleep(0.01)

        assert span.finished is True
        span._set_attribute.assert_any_call("gpu.duration_ms", mock.ANY)
    finally:
        resolver.stop(timeout=2.0)


# ---------------------------------------------------------------------------
# Task 12: grad_comm bucket duration + bytes into summary
# ---------------------------------------------------------------------------


def test_chained_comm_hook_feeds_grad_comm_summary(monkeypatch):
    """The chained DDP comm hook pushes bucket_duration_ms and bytes_per_bucket
    into the grad_comm summary reservoirs.
    """
    from ddtrace import config
    from ddtrace import tracer
    from ddtrace.contrib.internal.pytorch import _distributed
    from ddtrace.contrib.internal.pytorch import _summary

    _summary.reset_all()
    config.pytorch.grad_comm_enabled = True
    monkeypatch.setattr(tracer, "flush", lambda *a, **kw: None)

    class FakeBucket:
        def gradients(self):
            class FakeGrad:
                def numel(self):
                    return 1000

                def element_size(self):
                    return 4  # float32

            return [FakeGrad(), FakeGrad()]

    def user_hook(state, bucket):
        return None

    chained = _distributed._make_chained_comm_hook(user_hook)
    chained(None, FakeBucket())

    dur = _summary.drain_distribution("grad_comm.bucket_duration_ms")
    assert dur["count"] == 1

    bytes_snap = _summary.drain_distribution("grad_comm.bytes_per_bucket")
    assert bytes_snap["count"] == 1
    # 2 grads × 1000 elements × 4 bytes = 8000 bytes
    assert abs(bytes_snap["mean"] - 8000.0) < 0.01


def test_chained_comm_hook_summary_feeds_when_no_bytes(monkeypatch):
    """When bucket bytes can't be introspected, bucket_duration_ms still
    feeds the reservoir; bytes_per_bucket does not.
    """
    from ddtrace import config
    from ddtrace import tracer
    from ddtrace.contrib.internal.pytorch import _distributed
    from ddtrace.contrib.internal.pytorch import _summary

    _summary.reset_all()
    config.pytorch.grad_comm_enabled = True
    monkeypatch.setattr(tracer, "flush", lambda *a, **kw: None)

    class EmptyBucket:
        def gradients(self):
            return []

    chained = _distributed._make_chained_comm_hook(lambda s, b: None)
    chained(None, EmptyBucket())

    assert _summary.drain_distribution("grad_comm.bucket_duration_ms")["count"] == 1
    assert _summary.drain_distribution("grad_comm.bytes_per_bucket")["count"] == 0


# ---------------------------------------------------------------------------
# Task 13: rank-root rotation drains training-metric reservoirs
# ---------------------------------------------------------------------------


def test_rank_root_close_stamps_training_summary_facets(monkeypatch):
    """End-to-end: push values into the summary reservoirs, then close
    the rank-root span; assert summary facets land on the closed span.
    """
    from ddtrace import tracer
    from ddtrace.contrib.internal.pytorch import _rank_root
    from ddtrace.contrib.internal.pytorch import _summary

    _summary.reset_all()
    _summary.push_distribution("step.duration_ms", 50.0)
    _summary.push_distribution("step.forward_ms", 5.0)
    _summary.push_gauge("optim.learning_rate", 0.01)
    _summary.push_distribution("train.loss", 0.42)

    captured = {}

    class FakeContext:
        sampling_priority = 1

    class FakeSpan:
        def __init__(self):
            self.context = FakeContext()

        def set_tag(self, k, v=None):
            captured[k] = v

        def _set_attribute(self, k, v):
            captured[k] = v

        def finish(self):
            pass

    monkeypatch.setattr(tracer, "start_span", lambda *a, **kw: FakeSpan())
    _rank_root._span = None
    _rank_root.open(rank=0, world_size=1, framework="none", training_job_id="t1")
    _rank_root.close()

    # The summary facets should appear on the closed span.
    assert captured.get("step.duration_ms.count") == 1
    assert "step.duration_ms.p50_ms" in captured
    assert "step.duration_ms.mean_ms" in captured
    assert captured.get("step.forward_ms.count") == 1
    assert captured.get("optim.learning_rate.count") == 1
    assert "optim.learning_rate.last" in captured
    assert captured.get("train.loss.count") == 1
    assert "train.loss.p50" in captured  # no _ms suffix (not a duration metric)


def test_rank_root_close_skips_empty_reservoirs(monkeypatch):
    """When no values have been pushed, no summary facets appear."""
    from ddtrace import tracer
    from ddtrace.contrib.internal.pytorch import _rank_root
    from ddtrace.contrib.internal.pytorch import _summary

    _summary.reset_all()  # nothing pushed

    captured = {}

    class FakeContext:
        sampling_priority = 1

    class FakeSpan:
        def __init__(self):
            self.context = FakeContext()

        def set_tag(self, k, v=None):
            captured[k] = v

        def _set_attribute(self, k, v):
            captured[k] = v

        def finish(self):
            pass

    monkeypatch.setattr(tracer, "start_span", lambda *a, **kw: FakeSpan())
    _rank_root._span = None
    _rank_root.open(rank=0, world_size=1, framework="none", training_job_id="t1")
    _rank_root.close()

    # No summary facets should appear (other rank-root tags still do).
    for k in list(captured.keys()):
        assert not k.startswith("step."), f"unexpected summary facet: {k}"
        assert not k.startswith("train."), f"unexpected summary facet: {k}"
        assert not k.startswith("optim."), f"unexpected summary facet: {k}"
        assert not k.startswith("memory."), f"unexpected summary facet: {k}"


# ---------------------------------------------------------------------------
# Task 14: install gate + DD_PYTORCH_SUMMARY_PROFILING env var
# ---------------------------------------------------------------------------


def test_summary_profiling_default_is_true(monkeypatch):
    """DD_PYTORCH_SUMMARY_PROFILING defaults to true so summary mode is on
    out of the box.
    """
    # Force re-read by importing fresh.
    monkeypatch.delenv("DD_PYTORCH_SUMMARY_PROFILING", raising=False)
    import importlib

    from ddtrace import config

    config._integration_configs.pop("pytorch", None)
    import ddtrace.contrib.internal.pytorch as _pt_mod

    importlib.reload(_pt_mod)

    assert config.pytorch.summary_profiling is True


def test_summary_profiling_off_skips_layer2_install(monkeypatch):
    """When summary AND verbose are both off (and L1 is off), Layer-2 wraps
    do NOT install.
    """
    import torch

    monkeypatch.setenv("DD_PYTORCH_FORCE_INSTALL", "true")
    monkeypatch.setenv("DD_PYTORCH_SUMMARY_PROFILING", "false")
    monkeypatch.setenv("DD_PYTORCH_PROFILING", "false")
    monkeypatch.setenv("DD_PYTORCH_COLLECTIVE_TRACE", "false")

    # Reload config so the env vars take effect.
    from ddtrace import config

    config._integration_configs.pop("pytorch", None)
    import importlib

    import ddtrace.contrib.internal.pytorch as _pt_mod

    importlib.reload(_pt_mod)

    from ddtrace.contrib.internal.pytorch.patch import patch
    from ddtrace.contrib.internal.pytorch.patch import unpatch

    if getattr(torch, "_datadog_patch", False):
        unpatch()
    patch()
    try:
        # Tensor.backward not wrapped when all three flags off.
        assert not hasattr(torch.Tensor.backward, "__wrapped__")
    finally:
        unpatch()


def test_summary_profiling_on_installs_layer2(monkeypatch):
    """Default mode (summary on, verbose off, L1 off) installs the
    Layer-2 wraps so summary reservoirs receive feeds.
    """
    import torch

    monkeypatch.setenv("DD_PYTORCH_FORCE_INSTALL", "true")
    monkeypatch.setenv("DD_PYTORCH_SUMMARY_PROFILING", "true")
    monkeypatch.setenv("DD_PYTORCH_PROFILING", "false")
    monkeypatch.setenv("DD_PYTORCH_COLLECTIVE_TRACE", "false")

    from ddtrace import config

    config._integration_configs.pop("pytorch", None)
    import importlib

    import ddtrace.contrib.internal.pytorch as _pt_mod

    importlib.reload(_pt_mod)

    from ddtrace.contrib.internal.pytorch.patch import patch
    from ddtrace.contrib.internal.pytorch.patch import unpatch

    if getattr(torch, "_datadog_patch", False):
        unpatch()
    patch()
    try:
        assert hasattr(torch.Tensor.backward, "__wrapped__")
        assert hasattr(torch.optim.Optimizer.__init__, "__wrapped__")
    finally:
        unpatch()
