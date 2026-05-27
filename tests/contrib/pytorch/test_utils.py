import threading
from unittest import mock

from ddtrace.contrib.internal.pytorch._utils import _FRAMEWORK_REGISTRY
from ddtrace.contrib.internal.pytorch._utils import TRAINING_JOB_ID_TAG
from ddtrace.contrib.internal.pytorch._utils import _enter_framework
from ddtrace.contrib.internal.pytorch._utils import _get_active_framework
from ddtrace.contrib.internal.pytorch._utils import _instrumentation_bypass
from ddtrace.contrib.internal.pytorch._utils import _should_record_cuda_event
from ddtrace.contrib.internal.pytorch._utils import compute_clock_offset_ns
from ddtrace.contrib.internal.pytorch._utils import is_instrumentation_bypassed
from ddtrace.contrib.internal.pytorch._utils import job_id_env_set
from ddtrace.contrib.internal.pytorch._utils import register_framework
from ddtrace.contrib.internal.pytorch._utils import resolve_job_id_from_env
from ddtrace.contrib.internal.pytorch._utils import set_training_job_id_tag


def test_bypass_sets_and_clears_flag():
    assert is_instrumentation_bypassed() is False
    with _instrumentation_bypass():
        assert is_instrumentation_bypassed() is True
    assert is_instrumentation_bypassed() is False


def test_bypass_is_reentrant():
    with _instrumentation_bypass():
        with _instrumentation_bypass():
            assert is_instrumentation_bypassed() is True
        assert is_instrumentation_bypassed() is True
    assert is_instrumentation_bypassed() is False


def test_bypass_is_thread_local():
    seen = {}

    def worker():
        seen["child"] = is_instrumentation_bypassed()

    with _instrumentation_bypass():
        t = threading.Thread(target=worker)
        t.start()
        t.join()
    assert seen["child"] is False


class _DummyModel:
    """Tiny class registerable as a framework instance (must support __weakref__)."""


def test_framework_registry_priority():
    a, b, c = _DummyModel(), _DummyModel(), _DummyModel()
    register_framework(a, "ddp")
    register_framework(b, "fsdp")
    register_framework(c, "deepspeed")
    with _enter_framework(a):
        assert _get_active_framework() == "ddp"
        with _enter_framework(b):
            assert _get_active_framework() == "fsdp"
            with _enter_framework(c):
                assert _get_active_framework() == "deepspeed"
            assert _get_active_framework() == "fsdp"
        assert _get_active_framework() == "ddp"
    assert _get_active_framework() is None


def test_framework_registry_uses_weak_refs():
    m = _DummyModel()
    register_framework(m, "ddp")
    assert m in _FRAMEWORK_REGISTRY
    del m
    import gc

    gc.collect()
    # WeakKeyDictionary should have evicted the entry.
    # (We don't iterate the dict to count; we just check that a fresh instance
    # is not present, demonstrating the previous entry is gone.)
    assert len(_FRAMEWORK_REGISTRY) == 0


def test_get_active_framework_returns_none_when_empty():
    assert _get_active_framework() is None


def test_framework_stack_isolates_across_threads():
    import threading as _th

    a = _DummyModel()
    register_framework(a, "ddp")
    seen = {}

    def worker():
        seen["child"] = _get_active_framework()

    with _enter_framework(a):
        t = _th.Thread(target=worker)
        t.start()
        t.join()
    # The other thread's stack is independent; it sees no framework.
    assert seen["child"] is None


def test_should_record_returns_false_when_cuda_unavailable():
    tensor = mock.Mock(is_cuda=True)
    group = mock.Mock()
    with mock.patch("torch.cuda.is_available", return_value=False):
        assert _should_record_cuda_event(group, tensor) is False


def test_should_record_returns_false_for_gloo_backend():
    tensor = mock.Mock(is_cuda=True)
    group = mock.Mock()
    with (
        mock.patch("torch.cuda.is_available", return_value=True),
        mock.patch("torch.distributed.get_backend", return_value="gloo"),
    ):
        assert _should_record_cuda_event(group, tensor) is False


def test_should_record_returns_false_for_mpi_backend():
    tensor = mock.Mock(is_cuda=True)
    group = mock.Mock()
    with (
        mock.patch("torch.cuda.is_available", return_value=True),
        mock.patch("torch.distributed.get_backend", return_value="mpi"),
    ):
        assert _should_record_cuda_event(group, tensor) is False


def test_should_record_returns_true_for_nccl_cuda_tensor():
    tensor = mock.Mock(is_cuda=True)
    group = mock.Mock()
    with (
        mock.patch("torch.cuda.is_available", return_value=True),
        mock.patch("torch.distributed.get_backend", return_value="nccl"),
    ):
        assert _should_record_cuda_event(group, tensor) is True


def test_should_record_returns_false_for_cpu_tensor():
    tensor = mock.Mock(is_cuda=False)
    group = mock.Mock()
    with (
        mock.patch("torch.cuda.is_available", return_value=True),
        mock.patch("torch.distributed.get_backend", return_value="nccl"),
    ):
        assert _should_record_cuda_event(group, tensor) is False


def test_compute_clock_offset_returns_finite_offset_and_uncertainty():
    result = compute_clock_offset_ns()
    assert isinstance(result.offset_ns, int)
    assert isinstance(result.uncertainty_ns, int)
    assert result.uncertainty_ns >= 0


def test_compute_clock_offset_picks_smallest_uncertainty_sample():
    # 3 sandwiches: gaps of 2000, 200, 2000 ns -> uncertainties 1000, 100, 1000.
    # time_ns reads: 10_000, 20_000, 30_000.
    # The middle sample wins: offset = 20_000 - (2_000 + 2_200) // 2 = 20_000 - 2100 = 17_900.
    perf = iter([0, 2_000, 2_000, 2_200, 2_200, 4_200])
    times = iter([10_000, 20_000, 30_000])
    with (
        mock.patch("time.perf_counter_ns", lambda: next(perf)),
        mock.patch("time.time_ns", lambda: next(times)),
    ):
        result = compute_clock_offset_ns(samples=3)
    assert result.uncertainty_ns == 100
    assert result.offset_ns == 17_900


def test_compute_clock_offset_warns_when_uncertainty_exceeds_threshold(caplog):
    # All 3 sandwiches have a 4ms perf gap -> uncertainty 2ms (> 1ms threshold).
    perf = iter([0, 4_000_000] * 3)
    times = iter([1_000, 1_000, 1_000])
    with (
        mock.patch("time.perf_counter_ns", lambda: next(perf)),
        mock.patch("time.time_ns", lambda: next(times)),
        caplog.at_level("WARNING"),
    ):
        compute_clock_offset_ns(samples=3)
    assert any("uncertainty" in rec.message.lower() for rec in caplog.records)


def test_resolve_job_id_uses_dd_pytorch_job_id(monkeypatch):
    monkeypatch.setenv("DD_PYTORCH_JOB_ID", "explicit-id")
    monkeypatch.setenv("TORCHELASTIC_RUN_ID", "elastic-id")
    monkeypatch.setenv("SLURM_JOB_ID", "slurm-id")
    assert resolve_job_id_from_env() == "explicit-id"


def test_resolve_job_id_falls_back_to_torchelastic(monkeypatch):
    monkeypatch.delenv("DD_PYTORCH_JOB_ID", raising=False)
    monkeypatch.delenv("RAY_JOB_ID", raising=False)
    monkeypatch.setenv("TORCHELASTIC_RUN_ID", "elastic-id")
    monkeypatch.setenv("KUBEFLOW_TRAINING_JOB_ID", "kf-id")
    monkeypatch.setenv("SLURM_JOB_ID", "slurm-id")
    assert resolve_job_id_from_env() == "elastic-id"


def test_resolve_job_id_falls_back_to_kubeflow(monkeypatch):
    monkeypatch.delenv("DD_PYTORCH_JOB_ID", raising=False)
    monkeypatch.delenv("RAY_JOB_ID", raising=False)
    monkeypatch.delenv("TORCHELASTIC_RUN_ID", raising=False)
    monkeypatch.setenv("KUBEFLOW_TRAINING_JOB_ID", "kf-id")
    monkeypatch.setenv("SLURM_JOB_ID", "slurm-id")
    assert resolve_job_id_from_env() == "kf-id"


def test_resolve_job_id_falls_back_to_slurm(monkeypatch):
    for v in ("DD_PYTORCH_JOB_ID", "TORCHELASTIC_RUN_ID", "KUBEFLOW_TRAINING_JOB_ID", "RAY_JOB_ID"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("SLURM_JOB_ID", "slurm-id")
    assert resolve_job_id_from_env() == "slurm-id"


def test_resolve_job_id_generates_uuid_when_unset(monkeypatch):
    for v in (
        "DD_PYTORCH_JOB_ID",
        "TORCHELASTIC_RUN_ID",
        "KUBEFLOW_TRAINING_JOB_ID",
        "RAY_JOB_ID",
        "SLURM_JOB_ID",
    ):
        monkeypatch.delenv(v, raising=False)
    job_id = resolve_job_id_from_env()
    # UUID4 form has 36 chars including hyphens.
    assert len(job_id) == 36 and job_id.count("-") == 4


def test_resolve_job_id_strips_whitespace_and_truncates(monkeypatch):
    for v in (
        "DD_PYTORCH_JOB_ID",
        "TORCHELASTIC_RUN_ID",
        "KUBEFLOW_TRAINING_JOB_ID",
        "RAY_JOB_ID",
        "SLURM_JOB_ID",
    ):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("SLURM_JOB_ID", "  slurm-12345\n")
    assert resolve_job_id_from_env() == "slurm-12345"

    monkeypatch.setenv("DD_PYTORCH_JOB_ID", "x" * 500)
    assert resolve_job_id_from_env() == "x" * 200


def test_resolve_job_id_empty_string_falls_through(monkeypatch):
    monkeypatch.setenv("DD_PYTORCH_JOB_ID", "   ")  # whitespace-only treated as unset
    monkeypatch.setenv("TORCHELASTIC_RUN_ID", "elastic-id")
    assert resolve_job_id_from_env() == "elastic-id"


def test_should_record_handles_list_of_tensors():
    # all_gather etc. pass List[Tensor]; peek at the first element.
    cuda_list = [mock.Mock(is_cuda=True), mock.Mock(is_cuda=True)]
    cpu_list = [mock.Mock(is_cuda=False)]
    group = mock.Mock()
    with (
        mock.patch("torch.cuda.is_available", return_value=True),
        mock.patch("torch.distributed.get_backend", return_value="nccl"),
    ):
        assert _should_record_cuda_event(group, cuda_list) is True
        assert _should_record_cuda_event(group, cpu_list) is False
        assert _should_record_cuda_event(group, []) is False


def test_resolve_job_id_prefers_ray_over_torchelastic(monkeypatch):
    monkeypatch.delenv("DD_PYTORCH_JOB_ID", raising=False)
    monkeypatch.setenv("RAY_JOB_ID", "ray-abc")
    monkeypatch.setenv("TORCHELASTIC_RUN_ID", "te-xyz")
    monkeypatch.setenv("KUBEFLOW_TRAINING_JOB_ID", "kf-456")
    monkeypatch.setenv("SLURM_JOB_ID", "slurm-789")

    assert resolve_job_id_from_env() == "ray-abc"


def test_resolve_job_id_dd_override_still_wins_over_ray(monkeypatch):
    monkeypatch.setenv("DD_PYTORCH_JOB_ID", "user-fixed")
    monkeypatch.setenv("RAY_JOB_ID", "ray-abc")

    assert resolve_job_id_from_env() == "user-fixed"


def test_job_id_env_set_false_when_all_unset(monkeypatch):
    for v in ("DD_PYTORCH_JOB_ID", "RAY_JOB_ID", "TORCHELASTIC_RUN_ID", "KUBEFLOW_TRAINING_JOB_ID", "SLURM_JOB_ID"):
        monkeypatch.delenv(v, raising=False)
    assert job_id_env_set() is False


def test_job_id_env_set_true_for_each_chain_var(monkeypatch):
    for v in ("DD_PYTORCH_JOB_ID", "RAY_JOB_ID", "TORCHELASTIC_RUN_ID", "KUBEFLOW_TRAINING_JOB_ID", "SLURM_JOB_ID"):
        monkeypatch.delenv(v, raising=False)
    for v in ("DD_PYTORCH_JOB_ID", "RAY_JOB_ID", "TORCHELASTIC_RUN_ID", "KUBEFLOW_TRAINING_JOB_ID", "SLURM_JOB_ID"):
        monkeypatch.setenv(v, "x")
        assert job_id_env_set() is True, v
        monkeypatch.delenv(v)


def test_job_id_env_set_treats_whitespace_as_unset(monkeypatch):
    for v in ("DD_PYTORCH_JOB_ID", "RAY_JOB_ID", "TORCHELASTIC_RUN_ID", "KUBEFLOW_TRAINING_JOB_ID", "SLURM_JOB_ID"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("DD_PYTORCH_JOB_ID", "   \t\n")
    assert job_id_env_set() is False


def test_set_training_job_id_tag_sets_both_keys(monkeypatch):
    from ddtrace.contrib.internal.pytorch import _utils

    monkeypatch.setattr(_utils, "_default_job_id", "ray-abc-123", raising=False)
    monkeypatch.setattr(_utils._tls_job_id, "value", None, raising=False)

    class _FakeSpan:
        def __init__(self):
            self._tags = {}

        def set_tag(self, key, value):
            self._tags[key] = value

    span = _FakeSpan()
    set_training_job_id_tag(span)

    assert span._tags[TRAINING_JOB_ID_TAG] == "ray-abc-123"
    assert span._tags["job_id"] == "ray-abc-123"


def test_set_training_job_id_tag_noop_when_id_unset(monkeypatch):
    from ddtrace.contrib.internal.pytorch import _utils

    monkeypatch.setattr(_utils, "_default_job_id", None, raising=False)
    monkeypatch.setattr(_utils._tls_job_id, "value", None, raising=False)

    class _FakeSpan:
        def __init__(self):
            self._tags = {}

        def set_tag(self, key, value):
            self._tags[key] = value

    span = _FakeSpan()
    set_training_job_id_tag(span)
    assert span._tags == {}


def test_set_training_job_id_tag_does_not_acquire_lock(monkeypatch):
    """A6: this function runs per span on the hot path. The reads it
    performs must not take `_run_metadata_lock`.
    """
    from ddtrace.contrib.internal.pytorch import _utils

    _utils.set_cached_run_metadata(run_name="rn", submission_id="sub", metadata={"k": "v"})
    _utils.set_cached_job_id("training-abc")

    acquired = []
    real_lock = _utils._run_metadata_lock

    class WatchingLock:
        def acquire(self, *a, **kw):
            acquired.append("acquire")
            return real_lock.acquire(*a, **kw)

        def release(self):
            acquired.append("release")
            return real_lock.release()

        def __enter__(self):
            self.acquire()
            return self

        def __exit__(self, *a):
            self.release()

    monkeypatch.setattr(_utils, "_run_metadata_lock", WatchingLock())

    class FakeSpan:
        def __init__(self):
            self.tags = {}

        def set_tag(self, k, v=None):
            self.tags[k] = v

    for _ in range(100):
        s = FakeSpan()
        _utils.set_training_job_id_tag(s)
        assert s.tags.get("training_job.id") == "training-abc"

    assert acquired == [], f"hot-path span tagging took the lock: {acquired}"


def test_get_cached_run_metadata_is_immutable():
    """NB4: the published view must reject mutation."""
    import pytest

    from ddtrace.contrib.internal.pytorch import _utils

    _utils.set_cached_run_metadata(run_name="rn", submission_id="sub", metadata={"k": "v"})
    snap = _utils.get_cached_run_metadata()

    with pytest.raises(TypeError):
        snap["run_name"] = "mutated"
    with pytest.raises(TypeError):
        snap["metadata"]["k"] = "mutated"


def test_run_metadata_view_consistent_under_writer_load():
    """The view is replaced atomically; concurrent readers see either
    the old snapshot or the new one — never a torn intermediate state.
    """
    import threading

    from ddtrace.contrib.internal.pytorch import _utils

    _utils.set_cached_run_metadata(run_name="A", submission_id="A-sub", metadata={"k": "A"})

    ready = threading.Barrier(5)  # 4 readers + main
    stop = threading.Event()
    seen_inconsistent = []

    def reader():
        ready.wait(timeout=5)
        while not stop.is_set():
            snap = _utils.get_cached_run_metadata()
            rn = snap.get("run_name")
            sub = snap.get("submission_id")
            md = (snap.get("metadata") or {}).get("k")
            if rn is None or sub is None or md is None:
                seen_inconsistent.append(("missing", rn, sub, md))
                continue
            if not (rn == sub.split("-")[0] == md):
                seen_inconsistent.append((rn, sub, md))

    threads = [threading.Thread(target=reader) for _ in range(4)]
    for t in threads:
        t.start()
    try:
        ready.wait(timeout=5)
        for label in ("B", "C", "D", "E"):
            _utils.set_cached_run_metadata(run_name=label, submission_id=f"{label}-sub", metadata={"k": label})
    finally:
        stop.set()
        for t in threads:
            t.join(timeout=2)
    assert seen_inconsistent == [], f"saw torn reads: {seen_inconsistent[:5]}"
