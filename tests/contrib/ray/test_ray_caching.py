"""Verifies the memoization layer added to ray/core/utils.py."""

import pytest


@pytest.fixture(autouse=True)
def _clean_caches():
    from ddtrace.contrib.internal.ray.core import utils as u

    u._reset_thread_local_state()
    yield
    u._reset_thread_local_state()


def test_get_ray_service_name_reflects_env_changes(monkeypatch):
    """Service name is read fresh each call so multi-job drivers get the right name."""
    from ddtrace.contrib.internal.ray.core import utils as u

    monkeypatch.setenv(u.RAY_JOB_NAME, "job-A")
    assert u._get_ray_service_name() == "job-A"

    monkeypatch.setenv(u.RAY_JOB_NAME, "job-B")
    assert u._get_ray_service_name() == "job-B"


@pytest.mark.subprocess
def test_hostname_cached():
    """Subprocess: plant a sentinel in the cache and verify it is returned on subsequent calls.

    Without this sentinel, h1==h2==gethostname() would hold true even if caching was
    broken, since gethostname() always returns the same value in a given environment.
    """
    from ddtrace.contrib.internal.ray.core import utils as u

    # First call fills the cache with the real hostname.
    assert getattr(u._local, "hostname", None) is None
    u._get_cached_hostname()
    # Overwrite with a sentinel — subsequent calls must return this, not gethostname().
    u._local.hostname = "sentinel-host"
    assert u._get_cached_hostname() == "sentinel-host"
    assert u._get_cached_hostname() == "sentinel-host"


@pytest.mark.subprocess(
    env={
        "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
        "tracestate": "dd=s:1",
    }
)
def test_extract_traceparent_cached():
    """Subprocess: env vars set at process start; same Context object returned on every call."""
    from ddtrace.contrib.internal.ray.core import utils as u

    assert getattr(u._local, "tracing_context_keys", None) is None  # fresh thread-local state
    a = u._extract_tracing_context_from_env()
    assert u._local.tracing_context_keys is not None  # cache populated
    b = u._extract_tracing_context_from_env()
    c = u._extract_tracing_context_from_env()
    assert a is b is c


def test_inject_context_in_env_skips_when_unchanged(monkeypatch):
    """Repeated injection of the same context must not rewrite env vars."""
    from ddtrace._trace.context import Context
    from ddtrace.contrib.internal.ray.core import utils as u

    writes = []
    real_setitem = u.env.__setitem__

    def counting_setitem(key, value):
        writes.append((key, value))
        return real_setitem(key, value)

    monkeypatch.setattr(u.env, "__setitem__", counting_setitem)

    ctx = Context(trace_id=1234, span_id=5678, sampling_priority=1)
    u._inject_context_in_env(ctx)
    write_count_after_first = len(writes)
    u._inject_context_in_env(ctx)
    assert len(writes) == write_count_after_first


def test_inject_context_in_env_is_thread_safe(monkeypatch):
    """Two concurrent injectors must not raise; each thread tracks its own dedup state."""
    import threading

    from ddtrace._trace.context import Context
    from ddtrace.contrib.internal.ray.core import utils as u

    ctx_a = Context(trace_id=1, span_id=2)
    ctx_b = Context(trace_id=3, span_id=4)

    barrier = threading.Barrier(2)
    errors = []

    def hammer(ctx):
        try:
            barrier.wait(timeout=2)
            for _ in range(500):
                u._inject_context_in_env(ctx)
        except Exception as e:
            errors.append(e)

    t1 = threading.Thread(target=hammer, args=(ctx_a,))
    t2 = threading.Thread(target=hammer, args=(ctx_b,))
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)
    assert errors == []


def test_remote_function_does_not_call_inspect_signature_per_submit(monkeypatch):
    import inspect

    from ddtrace import config
    from ddtrace.contrib.internal.ray.core import remote_function

    # Mock config to avoid access errors
    fake_config = type("FakeConfig", (), {})()
    fake_config.submission_spans = False
    fake_config.integration_name = "ray"
    monkeypatch.setattr(config, "ray", fake_config)

    calls = []
    real_sig = inspect.signature

    def counting_signature(fn):
        calls.append(fn)
        return real_sig(fn)

    # Monkeypatch inspect.signature to track calls
    monkeypatch.setattr(inspect, "signature", counting_signature)

    class FakeInst:
        def __init__(self):
            import threading

            self._inject_lock = threading.Lock()
            self._function = lambda x: x
            self._function_name = "fake"
            self._function_signature = None
            self._is_cross_language = False
            self._function.__module__ = "test"

    inst = FakeInst()

    # First call: sets up _function_signature
    remote_function.traced_submit_task(lambda *a, **k: None, inst, (), {"args": (), "kwargs": {}})
    setup_calls = len(calls)

    # Second call: should NOT call inspect.signature (use cached _function_signature)
    remote_function.traced_submit_task(lambda *a, **k: None, inst, (), {"args": (), "kwargs": {}})

    assert len(calls) == setup_calls, "inspect.signature was called on the hot path"


def test_remote_function_handles_none_signature_defensively(monkeypatch):
    from ddtrace import config
    from ddtrace.contrib.internal.ray.core import remote_function

    # Mock config to avoid access errors
    fake_config = type("FakeConfig", (), {})()
    fake_config.submission_spans = False
    fake_config.integration_name = "ray"
    monkeypatch.setattr(config, "ray", fake_config)

    # Monkeypatch the function that would cause failures
    monkeypatch.setattr(
        "ddtrace.contrib.internal.ray.core.remote_function.core.dispatch_event",
        lambda *a, **k: None,
    )

    class FakeInst:
        def __init__(self):
            import threading

            self._inject_lock = threading.Lock()
            self._function = lambda x: x
            self._function_name = "fake"
            self._function_signature = None
            self._is_cross_language = False
            self._function.__module__ = "test"

    inst = FakeInst()
    # First call: sets up _function_signature
    remote_function.traced_submit_task(lambda *a, **k: None, inst, (), {"args": (), "kwargs": {}})
    # Simulate a case where _function_signature is reset to None
    inst._function_signature = None
    # Second call: must not raise even with None signature
    remote_function.traced_submit_task(lambda *a, **k: None, inst, (), {"args": (), "kwargs": {}})


def test_runtime_invariants_includes_namespace_and_version(monkeypatch):
    from ddtrace.contrib.internal.ray.core import utils as u

    class FakeRuntimeCtx:
        def get_job_id(self):
            return "job-X"

        def get_node_id(self):
            return "node-Y"

        def get_worker_id(self):
            return "worker-Z"

        @property
        def namespace(self):
            return "my-ns"

        @property
        def gcs_address(self):
            return "10.0.0.1:6379"

        @property
        def dashboard_url(self):
            return "http://10.0.0.1:8265"

    monkeypatch.setattr(u, "get_runtime_context", lambda: FakeRuntimeCtx())
    monkeypatch.setattr(u.ray, "is_initialized", lambda: True)
    monkeypatch.setattr(u.ray, "__version__", "2.46.0", raising=False)

    inv = u._get_runtime_invariants()
    assert inv.get("ray.namespace") == "my-ns"
    assert inv.get("ray.gcs_address") == "10.0.0.1:6379"
    assert inv.get("ray.dashboard_url") == "http://10.0.0.1:8265"
    assert inv.get("ray.version") == "2.46.0"


def test_runtime_invariants_not_cached_when_ray_not_initialized(monkeypatch):
    """Empty result when ray is not yet initialized must not be cached."""
    from ddtrace.contrib.internal.ray.core import utils as u

    monkeypatch.setattr(u.ray, "is_initialized", lambda: False)

    inv1 = u._get_runtime_invariants()
    assert inv1 == {}
    # Cache must still be None so the next call retries once Ray is up.
    assert getattr(u._local, "runtime_invariants", None) is None

    # Simulate Ray initializing between calls.
    class FakeRC:
        def get_job_id(self):
            return "job-late"

        def get_node_id(self):
            return "node-late"

        def get_worker_id(self):
            return None

    monkeypatch.setattr(u.ray, "is_initialized", lambda: True)
    monkeypatch.setattr(u, "get_runtime_context", lambda: FakeRC())

    inv2 = u._get_runtime_invariants()
    assert inv2.get("ray.job_id") == "job-late"
    assert getattr(u._local, "runtime_invariants", None) is not None


def test_runtime_invariants_invalidated_after_ray_reinit(monkeypatch):
    """Cache must be refilled after ray.shutdown() + ray.init() so stale tags are not emitted."""
    from ddtrace.contrib.internal.ray.core import utils as u

    class FakeRC:
        def __init__(self, job_id, node_id):
            self._job_id = job_id
            self._node_id = node_id

        def get_job_id(self):
            return self._job_id

        def get_node_id(self):
            return self._node_id

        def get_worker_id(self):
            return None

    monkeypatch.setattr(u.ray, "is_initialized", lambda: True)
    monkeypatch.setattr(u, "get_runtime_context", lambda: FakeRC("job-1", "node-A"))

    inv1 = u._get_runtime_invariants()
    assert inv1.get("ray.job_id") == "job-1"

    # Simulate ray.shutdown(): is_initialized() returns False, cache must be bypassed.
    monkeypatch.setattr(u.ray, "is_initialized", lambda: False)
    inv_down = u._get_runtime_invariants()
    assert inv_down == {}

    # Simulate ray.init() again with a new cluster context.
    monkeypatch.setattr(u.ray, "is_initialized", lambda: True)
    monkeypatch.setattr(u, "get_runtime_context", lambda: FakeRC("job-2", "node-B"))

    inv2 = u._get_runtime_invariants()
    assert inv2.get("ray.job_id") == "job-2", "stale job_id from prior Ray session returned"
    assert inv2.get("ray.node_id") == "node-B"


def test_runtime_invariants_invalidated_after_reinit_without_intermediate_call(monkeypatch):
    """Cache must be refilled even if _get_runtime_invariants() was never called during shutdown.

    The previous test covers the case where _get_runtime_invariants() IS called while
    is_initialized() returns False. This test covers the harder case: ray shuts down and
    reinits so quickly (or spans are only generated after reinit) that the shutdown window
    is never observed. Without node_id validation on the cache hit path, stale tags would
    be silently returned for the new session.
    """
    from ddtrace.contrib.internal.ray.core import utils as u

    class FakeRC:
        def __init__(self, job_id, node_id):
            self._job_id = job_id
            self._node_id = node_id

        def get_job_id(self):
            return self._job_id

        def get_node_id(self):
            return self._node_id

        def get_worker_id(self):
            return None

    # Session A: populate cache.
    monkeypatch.setattr(u.ray, "is_initialized", lambda: True)
    monkeypatch.setattr(u, "get_runtime_context", lambda: FakeRC("job-1", "node-A"))
    inv1 = u._get_runtime_invariants()
    assert inv1.get("ray.job_id") == "job-1"

    # Ray shuts down and reinits — _get_runtime_invariants() is never called during shutdown.
    # is_initialized() stays True from our perspective (reinit happened too fast to observe).
    monkeypatch.setattr(u, "get_runtime_context", lambda: FakeRC("job-2", "node-B"))

    inv2 = u._get_runtime_invariants()
    assert inv2.get("ray.job_id") == "job-2", "stale job_id returned despite new Ray session"
    assert inv2.get("ray.node_id") == "node-B"
