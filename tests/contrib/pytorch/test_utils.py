from ddtrace.contrib.internal.pytorch._utils import TRAINING_JOB_ID_TAG
from ddtrace.contrib.internal.pytorch._utils import job_id_env_set
from ddtrace.contrib.internal.pytorch._utils import resolve_job_id_from_env
from ddtrace.contrib.internal.pytorch._utils import set_training_job_id_tag


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


def test_resolve_job_id_empty_string_falls_through(monkeypatch):
    monkeypatch.setenv("DD_PYTORCH_JOB_ID", "   ")  # whitespace-only treated as unset
    monkeypatch.setenv("TORCHELASTIC_RUN_ID", "elastic-id")
    assert resolve_job_id_from_env() == "elastic-id"


def test_dd_pytorch_job_id_wins_over_ray_job_id(monkeypatch):
    monkeypatch.setenv("DD_PYTORCH_JOB_ID", "user-supplied-id")
    monkeypatch.setenv("RAY_JOB_ID", "33000000")
    assert resolve_job_id_from_env() == "user-supplied-id"


def test_resolve_job_id_prefers_ray_over_torchelastic(monkeypatch):
    monkeypatch.delenv("DD_PYTORCH_JOB_ID", raising=False)
    monkeypatch.setenv("RAY_JOB_ID", "ray-abc")
    monkeypatch.setenv("TORCHELASTIC_RUN_ID", "te-xyz")
    monkeypatch.setenv("KUBEFLOW_TRAINING_JOB_ID", "kf-456")
    monkeypatch.setenv("SLURM_JOB_ID", "slurm-789")

    assert resolve_job_id_from_env() == "ray-abc"


def test_job_id_env_set_false_when_all_unset(monkeypatch):
    for v in ("DD_PYTORCH_JOB_ID", "RAY_JOB_ID", "TORCHELASTIC_RUN_ID", "KUBEFLOW_TRAINING_JOB_ID", "SLURM_JOB_ID"):
        monkeypatch.delenv(v, raising=False)
    assert job_id_env_set() is False


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

        def set_tag(self, key, value=None):
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

        def set_tag(self, key, value=None):
            self._tags[key] = value

    span = _FakeSpan()
    set_training_job_id_tag(span)
    assert "manual.keep" in span._tags
    assert TRAINING_JOB_ID_TAG not in span._tags


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
