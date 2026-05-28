"""Per-rank lifetime span for Layer Zero."""

from unittest import mock

import pytest

from ddtrace.contrib.internal.pytorch import _device
from ddtrace.contrib.internal.pytorch import _metrics
from ddtrace.contrib.internal.pytorch import _rank_root
from ddtrace.contrib.internal.pytorch import _test_helpers as _th


@pytest.fixture(autouse=True)
def _reset(tracer):
    _th.reset_device_cache()
    _th.close_rank_root()
    _th.reset_metrics_state()
    with (
        mock.patch.object(_device, "_cuda_is_available", return_value=False),
        mock.patch.object(_device, "_hostname", return_value="h-9"),
    ):
        _device.discover(local_rank=0)
    yield
    _th.close_rank_root()
    _th.reset_metrics_state()
    _th.reset_device_cache()


def test_open_creates_span_with_required_tags(tracer):
    _rank_root.open(rank=3, world_size=8, framework="ddp", training_job_id="job-X")
    span = _th.current_rank_span()
    assert span is not None
    assert span.name == "pytorch.rank"
    assert span.get_tag("training_job.id") == "job-X"
    assert span.get_metric("rank") == 3
    assert span.get_metric("world_size") == 8
    assert span.get_tag("framework") == "ddp"
    assert span.get_tag("device.id") == "h-9:cpu"


def test_open_is_idempotent(tracer):
    _rank_root.open(rank=0, world_size=1, framework="none", training_job_id="job-X")
    first = _th.current_rank_span()
    _rank_root.open(rank=0, world_size=1, framework="none", training_job_id="job-X")
    second = _th.current_rank_span()
    assert first is second


def test_close_finishes_span(tracer):
    _rank_root.open(rank=0, world_size=1, framework="none", training_job_id="job-X")
    span = _th.current_rank_span()
    _rank_root.close()
    assert span.finished
    assert _th.current_rank_span() is None


def test_close_without_open_is_safe(tracer):
    _rank_root.close()  # no error


def test_open_registers_atexit_handler(tracer, monkeypatch):
    """Many users never call `unpatch()` (a `ddtrace-run` process just
    exits). We register `close` as an atexit hook so the rank span is
    finished cleanly on normal interpreter shutdown.
    """
    handlers = []
    real_register = _rank_root.atexit.register

    def capture(fn, *a, **kw):
        handlers.append(fn)
        return real_register(fn, *a, **kw)

    monkeypatch.setattr(_rank_root.atexit, "register", capture)
    _th.set_atexit_registered(False)
    _rank_root.open(rank=0, world_size=1, framework="none", training_job_id="job-X")
    assert _rank_root.close in handlers


def test_atexit_register_unregister_balanced_across_cycles(tracer, monkeypatch):
    """``close()`` must ``atexit.unregister`` so multiple open/close cycles
    don't accumulate handlers in the atexit list — only one ``close``
    callback should be live between cycles.
    """
    registered = 0
    unregistered = 0
    real_register = _rank_root.atexit.register
    real_unregister = _rank_root.atexit.unregister

    def capture_register(fn, *a, **kw):
        nonlocal registered
        if fn is _rank_root.close:
            registered += 1
        return real_register(fn, *a, **kw)

    def capture_unregister(fn):
        nonlocal unregistered
        if fn is _rank_root.close:
            unregistered += 1
        return real_unregister(fn)

    monkeypatch.setattr(_rank_root.atexit, "register", capture_register)
    monkeypatch.setattr(_rank_root.atexit, "unregister", capture_unregister)
    _th.set_atexit_registered(False)
    _rank_root.open(rank=0, world_size=1, framework="none", training_job_id="job-X")
    _rank_root.close()
    _rank_root.open(rank=0, world_size=1, framework="none", training_job_id="job-X")
    _rank_root.close()
    assert registered == 2
    assert unregistered == 2


def test_set_framework_updates_open_span_tag(tracer):
    _rank_root.open(rank=0, world_size=1, framework="none", training_job_id="job-X")
    span = _th.current_rank_span()
    assert span.get_tag("framework") == "none"
    _rank_root.set_framework("ddp")
    assert span.get_tag("framework") == "ddp"


def test_set_framework_noop_without_open_span(tracer):
    _rank_root.set_framework("ddp")  # no error


def test_set_framework_noop_for_empty_string(tracer):
    _rank_root.open(rank=0, world_size=1, framework="none", training_job_id="job-X")
    _rank_root.set_framework("")
    assert _th.current_rank_span().get_tag("framework") == "none"


def test_close_attaches_collective_percentile_metrics(tracer):
    """At close(), the rank span should carry per-op percentile metrics as
    flat numeric facets so a job-scoped investigator can spot stragglers
    via spans aggregate queries (no client-side JSON parsing).
    """
    _rank_root.open(rank=0, world_size=1, framework="ddp", training_job_id="job-X")
    span = _th.current_rank_span()
    for v in (0.30, 0.32, 0.38, 0.40, 0.51):
        _metrics.record_collective(op="allreduce", duration_ms=v, bytes_count=64)
    _rank_root.close()
    # Per-op percentile metrics.
    for pct in ("p10", "p50", "p90", "p99"):
        assert span.get_metric(f"collective.allreduce.{pct}_ms") is not None
    assert span.get_metric("collective.allreduce.n") == 5
    # Cross-op rollups.
    assert span.get_metric("collective.p99_max_ms") == span.get_metric("collective.allreduce.p99_ms")
    assert span.get_metric("collective.ops_count") == 1
    # JSON summary tag is gone.
    assert span.get_tag("collective.summary") is None


def test_close_with_dotted_op_name_sanitizes_facet(tracer):
    """Op names containing dots must be sanitized so the resulting facet
    is a single attribute path, not a nested one.
    """
    _rank_root.open(rank=0, world_size=1, framework="ddp", training_job_id="job-X")
    span = _th.current_rank_span()
    _metrics.record_collective(op="grad.comm", duration_ms=0.5, bytes_count=64)
    _rank_root.close()
    assert span.get_metric("collective.grad_comm.p99_ms") is not None
    assert span.get_metric("collective.grad.comm.p99_ms") is None


def test_close_p99_max_picks_worst_across_ops(tracer):
    """`collective.p99_max_ms` is the max p99 across every op on the rank
    — that's the rollup straggler queries aggregate on.
    """
    _rank_root.open(rank=0, world_size=1, framework="ddp", training_job_id="job-X")
    span = _th.current_rank_span()
    for v in (0.10, 0.11, 0.12):
        _metrics.record_collective(op="allreduce", duration_ms=v, bytes_count=64)
    for v in (5.0, 6.0, 7.0):
        _metrics.record_collective(op="broadcast", duration_ms=v, bytes_count=64)
    _rank_root.close()
    assert span.get_metric("collective.p99_max_ms") == span.get_metric("collective.broadcast.p99_ms")
    assert span.get_metric("collective.ops_count") == 2


def test_close_without_samples_omits_summary_metrics(tracer):
    """A rank that finished before any collective was recorded should not
    carry any collective.* metrics — that's just noise.
    """
    _rank_root.open(rank=0, world_size=1, framework="ddp", training_job_id="job-X")
    span = _th.current_rank_span()
    _rank_root.close()
    assert span.get_tag("collective.summary") is None
    assert span.get_metric("collective.p99_max_ms") is None
    assert span.get_metric("collective.ops_count") is None


def test_ray_run_context_tagged_at_open_when_cache_populated_early(tracer):
    """Driver-side path: the Ray Train fit wrapper populates the cache
    before ``init_process_group`` fires, so the tags land at open.
    """
    from ddtrace.contrib.internal.pytorch import _utils

    _utils.set_cached_run_metadata(
        submission_id="raysubmit_early",
        metadata={"job_name": "early.job"},
        run_name="run-early",
    )
    try:
        _rank_root.open(rank=0, world_size=1, framework="ddp", training_job_id="job-X")
        span = _th.current_rank_span()
        assert span.get_tag("ray.submission_id") == "raysubmit_early"
        assert span.get_tag("ray.metadata.job_name") == "early.job"
        assert span.get_tag("ray.train.run_name") == "run-early"
    finally:
        _utils.clear_cached_run_metadata()


def test_ray_run_context_backfilled_at_close_when_cache_populated_late(tracer):
    """Worker-side path: Ray Train calls ``init_process_group`` itself
    *before* invoking the wrapped train function, so the cache is empty
    when the rank span opens. The wrapper populates the cache later,
    and ``close()`` must backfill the tags before finishing the span.
    """
    from ddtrace.contrib.internal.pytorch import _utils

    # Cache empty at open time.
    _utils.clear_cached_run_metadata()
    _rank_root.open(rank=0, world_size=1, framework="ddp", training_job_id="job-X")
    span = _th.current_rank_span()
    assert span.get_tag("ray.submission_id") is None
    assert span.get_tag("ray.metadata.job_name") is None

    # Wrapper fires after the rank span is already open.
    _utils.set_cached_run_metadata(
        submission_id="raysubmit_late",
        metadata={"job_name": "late.job"},
        run_name="run-late",
    )
    try:
        _rank_root.close()
        assert span.get_tag("ray.submission_id") == "raysubmit_late"
        assert span.get_tag("ray.metadata.job_name") == "late.job"
        assert span.get_tag("ray.train.run_name") == "run-late"
    finally:
        _utils.clear_cached_run_metadata()


def test_retag_ray_run_context_tags_live_rank_span(tracer):
    """Regression: ``ray.submission_id`` was missing on ``pytorch.rank``
    in live verification because ``_run_train_func_in_worker`` restores
    the cache to empty before ``_rank_root.close()`` runs at exit. The
    new ``retag_ray_run_context()`` entrypoint is called by the worker
    wrap immediately after populating the cache so the tag lands on the
    live span (not at close, which sees an empty cache).
    """
    from ddtrace.contrib.internal.pytorch import _utils

    _utils.clear_cached_run_metadata()
    _rank_root.open(rank=0, world_size=1, framework="ddp", training_job_id="job-X")
    span = _th.current_rank_span()
    assert span.get_tag("ray.submission_id") is None

    # Worker wrap populates the cache, then immediately calls retag.
    _utils.set_cached_run_metadata(
        submission_id="raysubmit_eager",
        metadata={"job_name": "eager.job"},
        run_name="run-eager",
    )
    try:
        _rank_root.retag_ray_run_context()
        assert span.get_tag("ray.submission_id") == "raysubmit_eager"
        assert span.get_tag("ray.metadata.job_name") == "eager.job"
        assert span.get_tag("ray.train.run_name") == "run-eager"

        # Simulate the worker wrap's finally clearing the cache (restore
        # to empty). The tags must stay on the live span — they were
        # written eagerly, not pulled at close.
        _utils.clear_cached_run_metadata()
        assert span.get_tag("ray.submission_id") == "raysubmit_eager"
    finally:
        _utils.clear_cached_run_metadata()
        _rank_root.close()


def test_close_logs_warning_on_per_facet_set_attribute_failure(tracer, caplog, monkeypatch):
    """When ``span._set_attribute`` rejects a summary facet, the failure
    must be visible at WARNING level (was previously debug-level, which
    silently masked grad_comm.bucket_duration_ms.{min_ms,max_ms,p*_ms}
    rejections in production).
    """
    import logging as _logging

    from ddtrace.contrib.internal.pytorch import _summary

    _summary.reset_all()
    _summary.push_distribution("step.forward_ms", 1.5)

    _rank_root.open(rank=0, world_size=1, framework="ddp", training_job_id="job-x")
    span = _th.current_rank_span()

    # Make _set_attribute raise for every facet so we exercise the warning path.
    original_set_attribute = type(span)._set_attribute

    def boom(self, key, value):
        if key.startswith("step.forward_ms"):
            raise ValueError(f"simulated rejection for {key}")
        return original_set_attribute(self, key, value)

    monkeypatch.setattr(type(span), "_set_attribute", boom, raising=False)

    with caplog.at_level(_logging.WARNING, logger="ddtrace.contrib.internal.pytorch._rank_root"):
        _rank_root.close()

    # At least one WARNING with the failing facet name + the value type.
    warnings = [r for r in caplog.records if r.levelno == _logging.WARNING]
    msgs = [r.getMessage() for r in warnings]
    assert any("step.forward_ms" in m for m in msgs), f"expected warning naming failed facet, got: {msgs}"


def test_retag_ray_run_context_noop_when_no_span_open(tracer):
    """retag_ray_run_context() must not crash when called with no rank
    span open (e.g., installed but workers never reach init_process_group).
    """
    from ddtrace.contrib.internal.pytorch import _utils

    # Ensure no span is open.
    try:
        _rank_root.close()
    except Exception:
        pass

    _utils.set_cached_run_metadata(submission_id="x", metadata={}, run_name="r")
    try:
        # Must not raise.
        _rank_root.retag_ray_run_context()
    finally:
        _utils.clear_cached_run_metadata()


def test_rank_root_nests_under_active_ray_worker_span(tracer):
    """When a `ray.train.worker` span is currently active, the
    `pytorch.rank` span should become its child (not a new trace root).
    """
    ray_worker = tracer.start_span("ray.train.worker", service="ray")
    tracer.context_provider.activate(ray_worker)
    try:
        _rank_root.open(rank=0, world_size=1, framework="ray", training_job_id="job-Y")
        rank_span = _th.current_rank_span()
        assert rank_span is not None
        # The rank-root span should share a trace_id with the ray worker.
        assert rank_span.trace_id == ray_worker.trace_id
        # And its parent_id should reference the ray worker's span_id.
        assert rank_span.parent_id == ray_worker.span_id
    finally:
        _rank_root.close()
        ray_worker.finish()
        tracer.context_provider.activate(None)


def test_rank_root_close_flush_is_bounded(monkeypatch):
    """A slow tracer.flush() must not extend rank-root close beyond a
    bounded timeout.
    """
    import threading
    import time

    from ddtrace import tracer
    from ddtrace.contrib.internal.pytorch import _rank_root

    block = threading.Event()

    def slow_flush(*args, **kwargs):
        block.wait(timeout=10)

    monkeypatch.setattr(tracer, "flush", slow_flush)

    _rank_root.open(rank=0, world_size=1, framework="none", training_job_id="t1")
    start = time.monotonic()
    _rank_root.close()
    elapsed = time.monotonic() - start
    block.set()
    assert elapsed < 2.0, f"close took {elapsed:.2f}s; expected bounded < 2s"


# ---------------------------------------------------------------------------
# Task 3: torch / cudnn / nccl / env / launcher / GPU invariant tagging
# ---------------------------------------------------------------------------


def test_detect_launcher_torchrun(monkeypatch):
    from ddtrace.contrib.internal.pytorch import _distributed

    monkeypatch.setenv("TORCHELASTIC_RUN_ID", "tr-123")
    monkeypatch.delenv("_RAY_SUBMISSION_ID", raising=False)
    monkeypatch.delenv("RAY_JOB_ID", raising=False)
    monkeypatch.delenv("SLURM_JOB_ID", raising=False)
    monkeypatch.delenv("KUBEFLOW_TRAINING_JOB_ID", raising=False)
    assert _distributed._detect_launcher() == "torchrun"


def test_detect_launcher_ray(monkeypatch):
    from ddtrace.contrib.internal.pytorch import _distributed

    monkeypatch.delenv("TORCHELASTIC_RUN_ID", raising=False)
    monkeypatch.setenv("_RAY_SUBMISSION_ID", "raysub-1")
    assert _distributed._detect_launcher() == "ray"


def test_detect_launcher_ray_job_id(monkeypatch):
    from ddtrace.contrib.internal.pytorch import _distributed

    monkeypatch.delenv("TORCHELASTIC_RUN_ID", raising=False)
    monkeypatch.delenv("_RAY_SUBMISSION_ID", raising=False)
    monkeypatch.setenv("RAY_JOB_ID", "rayjob-99")
    monkeypatch.delenv("SLURM_JOB_ID", raising=False)
    monkeypatch.delenv("KUBEFLOW_TRAINING_JOB_ID", raising=False)
    assert _distributed._detect_launcher() == "ray"


def test_detect_launcher_slurm(monkeypatch):
    from ddtrace.contrib.internal.pytorch import _distributed

    monkeypatch.delenv("TORCHELASTIC_RUN_ID", raising=False)
    monkeypatch.delenv("_RAY_SUBMISSION_ID", raising=False)
    monkeypatch.delenv("RAY_JOB_ID", raising=False)
    monkeypatch.setenv("SLURM_JOB_ID", "slurm-42")
    monkeypatch.delenv("KUBEFLOW_TRAINING_JOB_ID", raising=False)
    assert _distributed._detect_launcher() == "slurm"


def test_detect_launcher_kubeflow(monkeypatch):
    from ddtrace.contrib.internal.pytorch import _distributed

    monkeypatch.delenv("TORCHELASTIC_RUN_ID", raising=False)
    monkeypatch.delenv("_RAY_SUBMISSION_ID", raising=False)
    monkeypatch.delenv("RAY_JOB_ID", raising=False)
    monkeypatch.delenv("SLURM_JOB_ID", raising=False)
    monkeypatch.setenv("KUBEFLOW_TRAINING_JOB_ID", "kf-job-1")
    assert _distributed._detect_launcher() == "kubeflow"


def test_detect_launcher_none(monkeypatch):
    from ddtrace.contrib.internal.pytorch import _distributed

    for var in (
        "TORCHELASTIC_RUN_ID",
        "_RAY_SUBMISSION_ID",
        "RAY_JOB_ID",
        "SLURM_JOB_ID",
        "KUBEFLOW_TRAINING_JOB_ID",
    ):
        monkeypatch.delenv(var, raising=False)
    assert _distributed._detect_launcher() is None


def test_get_cached_backend_caches_result(monkeypatch):
    from ddtrace.contrib.internal.pytorch import _distributed

    # Reset the cache.
    _distributed._cached_distributed_backend = None
    monkeypatch.setattr(
        "ddtrace.contrib.internal.pytorch._distributed.torch.distributed.is_available",
        lambda: True,
    )
    monkeypatch.setattr(
        "ddtrace.contrib.internal.pytorch._distributed.torch.distributed.is_initialized",
        lambda: True,
    )
    monkeypatch.setattr(
        "ddtrace.contrib.internal.pytorch._distributed.torch.distributed.get_backend",
        lambda: "nccl",
    )
    result1 = _distributed._get_cached_backend()
    assert result1 == "nccl"
    # Second call should return cached value without calling get_backend again.
    monkeypatch.setattr(
        "ddtrace.contrib.internal.pytorch._distributed.torch.distributed.get_backend",
        lambda: "SHOULD_NOT_BE_CALLED",
    )
    result2 = _distributed._get_cached_backend()
    assert result2 == "nccl"
    # Clean up.
    _distributed._cached_distributed_backend = None


def test_get_cached_backend_returns_none_when_not_initialized(monkeypatch):
    from ddtrace.contrib.internal.pytorch import _distributed

    _distributed._cached_distributed_backend = None
    monkeypatch.setattr(
        "ddtrace.contrib.internal.pytorch._distributed.torch.distributed.is_available",
        lambda: True,
    )
    monkeypatch.setattr(
        "ddtrace.contrib.internal.pytorch._distributed.torch.distributed.is_initialized",
        lambda: False,
    )
    assert _distributed._get_cached_backend() is None
    _distributed._cached_distributed_backend = None


def test_rank_span_carries_torch_invariants(monkeypatch):
    """pytorch.rank span must carry torch version and cuDNN settings."""
    captured = {}

    class FakeSpan:
        def __init__(self):
            self.context = type("C", (), {"sampling_priority": 1})()

        def set_tag(self, k, v=None):
            captured[k] = v

        def _set_attribute(self, k, v):
            captured[k] = v

        def finish(self):
            pass

    fake = FakeSpan()
    from ddtrace import tracer

    monkeypatch.setattr(tracer, "start_span", lambda *a, **kw: fake)

    # Drop all NCCL / env vars so only torch/cudnn tags appear.
    for v in (
        "NCCL_DEBUG",
        "NCCL_SOCKET_IFNAME",
        "NCCL_IB_DISABLE",
        "NCCL_P2P_DISABLE",
        "NCCL_ALGO",
        "NCCL_PROTO",
        "TORCH_NCCL_ASYNC_ERROR_HANDLING",
        "CUDA_VISIBLE_DEVICES",
        "MASTER_ADDR",
        "LOCAL_RANK",
        "LOCAL_WORLD_SIZE",
        "GROUP_RANK",
        "GROUP_WORLD_SIZE",
        "MASTER_PORT",
        "TORCHELASTIC_RUN_ID",
        "_RAY_SUBMISSION_ID",
        "RAY_JOB_ID",
        "SLURM_JOB_ID",
        "KUBEFLOW_TRAINING_JOB_ID",
    ):
        monkeypatch.delenv(v, raising=False)

    _rank_root._span = None
    _rank_root.open(rank=0, world_size=1, framework="none", training_job_id="t1")

    # torch.__version__ is always populated; cudnn.{enabled,benchmark,deterministic} too.
    assert "torch.version" in captured
    assert "torch.cudnn.enabled" in captured

    _rank_root.close()


def test_rank_span_carries_env_signals(monkeypatch):
    """pytorch.rank span must carry NCCL/distributed env vars as tags/facets."""
    from ddtrace import tracer

    captured = {}

    class FakeSpan:
        def __init__(self):
            self.context = type("C", (), {"sampling_priority": 1})()

        def set_tag(self, k, v=None):
            captured[k] = v

        def _set_attribute(self, k, v):
            captured[k] = v

        def finish(self):
            pass

    monkeypatch.setattr(tracer, "start_span", lambda *a, **kw: FakeSpan())
    monkeypatch.setenv("NCCL_DEBUG", "INFO")
    monkeypatch.setenv("LOCAL_RANK", "3")
    monkeypatch.setenv("MASTER_ADDR", "10.0.0.5")
    monkeypatch.setenv("MASTER_PORT", "29500")

    _rank_root._span = None
    _rank_root.open(rank=0, world_size=1, framework="none", training_job_id="t1")
    assert captured.get("nccl.debug") == "INFO"
    assert captured.get("pytorch.local_rank") == 3
    assert captured.get("pytorch.master_addr") == "10.0.0.5"
    assert captured.get("pytorch.master_port") == 29500
    _rank_root.close()


def test_rank_span_carries_launcher_tag(monkeypatch):
    """pytorch.rank span must carry the `launcher` tag when a launcher env var is set."""
    from ddtrace import tracer

    captured = {}

    class FakeSpan:
        def __init__(self):
            self.context = type("C", (), {"sampling_priority": 1})()

        def set_tag(self, k, v=None):
            captured[k] = v

        def _set_attribute(self, k, v):
            captured[k] = v

        def finish(self):
            pass

    monkeypatch.setattr(tracer, "start_span", lambda *a, **kw: FakeSpan())
    # Clear all other launcher vars so only torchrun fires.
    for v in ("_RAY_SUBMISSION_ID", "RAY_JOB_ID", "SLURM_JOB_ID", "KUBEFLOW_TRAINING_JOB_ID"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("TORCHELASTIC_RUN_ID", "elastic-run-1")

    _rank_root._span = None
    _rank_root.open(rank=0, world_size=1, framework="none", training_job_id="t1")
    assert captured.get("launcher") == "torchrun"
    _rank_root.close()


def test_rank_span_carries_new_device_gpu_fields(monkeypatch):
    """pytorch.rank span must expose GPU DeviceInfo fields when populated."""
    from ddtrace import tracer
    from ddtrace.contrib.internal.pytorch._device import DeviceInfo

    captured = {}

    class FakeSpan:
        def __init__(self):
            self.context = type("C", (), {"sampling_priority": 1})()

        def set_tag(self, k, v=None):
            captured[k] = v

        def _set_attribute(self, k, v):
            captured[k] = v

        def finish(self):
            pass

    monkeypatch.setattr(tracer, "start_span", lambda *a, **kw: FakeSpan())

    # Inject a fake DeviceInfo with GPU fields.
    fake_info = DeviceInfo(
        device_id="gpu-uuid-abc",
        device_index=0,
        kind="cuda",
        hostname="node-1",
        gpu_name="NVIDIA A100",
        gpu_compute_capability="8.0",
        gpu_sm_count=108,
        gpu_total_memory_bytes=85899345920,
        gpu_driver_version="525.85.12",
    )
    monkeypatch.setattr(_device, "get", lambda: fake_info)

    _rank_root._span = None
    _rank_root.open(rank=0, world_size=1, framework="none", training_job_id="t1")
    assert captured.get("device.gpu.name") == "NVIDIA A100"
    assert captured.get("device.gpu.compute_capability") == "8.0"
    assert captured.get("device.gpu.sm_count") == 108
    assert captured.get("device.gpu.total_memory_bytes") == 85899345920
    assert captured.get("device.gpu.driver_version") == "525.85.12"
    _rank_root.close()
