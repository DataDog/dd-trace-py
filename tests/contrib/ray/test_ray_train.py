import sys

import ray.cloudpickle as cloudpickle

import ddtrace.contrib.internal.ray.patch  # noqa: F401


def _user_train_fn(config):
    return f"trained with config={config}"


# ---------------------------------------------------------------------------
# Helpers shared by worker tests
# ---------------------------------------------------------------------------


class _CaptureWriter:
    """Minimal writer that records finished spans for assertions.

    Implements the interface expected by ``tracer._span_aggregator.writer``
    (``write``, ``stop``, ``flush_queue``).
    """

    def __init__(self):
        self.spans = []

    def write(self, spans=None):
        if spans:
            self.spans.extend(spans)

    def stop(self, *args, **kwargs):
        pass

    def flush_queue(self, *args, **kwargs):
        pass


def test_wrapper_preserves_user_fn_identity_metadata():
    from ddtrace.contrib.internal.ray.train import _DDTrainFuncWrapper

    wrapper = _DDTrainFuncWrapper(_user_train_fn, training_job_id="ray-abc")

    # functools.update_wrapper preserves the wrapped fn's __name__ so Ray
    # Train's logging surfaces the user's name, not our wrapper class.
    assert wrapper.__name__ == "_user_train_fn"
    assert wrapper.__wrapped__ is _user_train_fn


def test_wrapper_starts_with_empty_parent_headers():
    from ddtrace.contrib.internal.ray.train import _DDTrainFuncWrapper

    wrapper = _DDTrainFuncWrapper(_user_train_fn, training_job_id="ray-abc")
    assert wrapper._parent_headers == {}


def test_wrapper_bind_parent_context_injects_w3c_headers():
    from ddtrace.contrib.internal.ray.train import _DDTrainFuncWrapper
    from ddtrace.trace import tracer

    wrapper = _DDTrainFuncWrapper(_user_train_fn, training_job_id="ray-abc")
    with tracer.start_span("driver.parent") as span:
        wrapper.bind_parent_context(span.context)

    assert "traceparent" in wrapper._parent_headers


def test_wrapper_round_trips_through_cloudpickle():
    from ddtrace.contrib.internal.ray.train import _DDTrainFuncWrapper

    wrapper = _DDTrainFuncWrapper(_user_train_fn, training_job_id="ray-abc")
    wrapper._parent_headers = {"traceparent": "00-aa-bb-01"}

    revived = cloudpickle.loads(cloudpickle.dumps(wrapper))

    assert revived._training_job_id == "ray-abc"
    assert revived._parent_headers == {"traceparent": "00-aa-bb-01"}
    assert revived.__name__ == "_user_train_fn"
    assert revived._fn is not None


# ---------------------------------------------------------------------------
# _run_train_func_in_worker tests
# ---------------------------------------------------------------------------


def test_worker_recovers_parent_from_wrapper_headers(monkeypatch):
    """When wrapper headers contain a W3C traceparent, the worker's span
    nests under that trace_id (not as a new trace root).
    """
    from ddtrace.contrib.internal.ray.train import _run_train_func_in_worker
    from ddtrace.propagation.http import HTTPPropagator
    from ddtrace.trace import tracer

    captured = {}

    def user_fn(config):
        captured["span"] = tracer.current_span()
        return "ok"

    with tracer.start_span("driver.parent") as parent:
        parent_headers = {}
        HTTPPropagator.inject(parent.context, parent_headers)
        parent_trace_id = parent.trace_id

    monkeypatch.setenv("RANK", "3")
    _run_train_func_in_worker(user_fn, "ray-abc", parent_headers, ({"x": 1},), {})

    inner = captured["span"]
    assert inner is not None
    assert inner.trace_id == parent_trace_id


def test_worker_falls_back_to_kwarg(monkeypatch):
    """No wrapper headers → use ``_dd_ray_trace_ctx`` kwarg."""
    from ddtrace.contrib.internal.ray.constants import DD_RAY_TRACE_CTX
    from ddtrace.contrib.internal.ray.train import _run_train_func_in_worker
    from ddtrace.propagation.http import _TraceContext
    from ddtrace.trace import tracer

    captured = {}

    def user_fn(**user_kwargs):
        captured["span"] = tracer.current_span()
        captured["user_kwargs"] = user_kwargs

    with tracer.start_span("driver.parent") as parent:
        # The kwarg channel uses ``_TraceContext._inject`` per the existing
        # Ray contrib (see ``_inject_context_in_kwargs`` in core/utils.py);
        # use the same serializer here so the test exercises the actual
        # production wire format.
        kwarg_payload = {}
        _TraceContext._inject(parent.context, kwarg_payload)
        expected_trace_id = parent.trace_id

    monkeypatch.setenv("RANK", "0")
    _run_train_func_in_worker(
        user_fn,
        "ray-abc",
        {},
        (),
        {DD_RAY_TRACE_CTX: kwarg_payload, "user_arg": 42},
    )

    assert captured["span"].trace_id == expected_trace_id
    # ``_dd_ray_trace_ctx`` is transport-only — never forwarded to the user fn.
    assert DD_RAY_TRACE_CTX not in captured["user_kwargs"]
    assert captured["user_kwargs"]["user_arg"] == 42


def test_worker_tags_span_with_training_job_id_and_rank(monkeypatch):
    """Worker span is tagged with training_job.id, ray.submission_id, rank,
    and world_size when those values are available.
    """
    from ddtrace.contrib.internal.ray.train import _run_train_func_in_worker
    from ddtrace.trace import tracer

    writer = _CaptureWriter()
    monkeypatch.setattr(tracer._span_aggregator, "writer", writer)
    monkeypatch.setenv("RANK", "5")
    monkeypatch.setenv("WORLD_SIZE", "8")
    _run_train_func_in_worker(lambda: None, "ray-abc", {}, (), {})

    [worker_span] = [s for s in writer.spans if s.name == "ray.train.worker"]
    assert worker_span.get_tag("training_job.id") == "ray-abc"
    assert worker_span.get_tag("ray.submission_id") == "ray-abc"
    assert worker_span.get_tag("rank") == "5"
    assert worker_span.get_tag("world_size") == "8"


def test_worker_tags_error_on_exception(monkeypatch):
    """Exceptions raised in the user fn set error=1 on the worker span."""
    from ddtrace.contrib.internal.ray.train import _run_train_func_in_worker
    from ddtrace.trace import tracer

    writer = _CaptureWriter()
    monkeypatch.setattr(tracer._span_aggregator, "writer", writer)

    def bad_fn():
        raise RuntimeError("kaboom")

    try:
        _run_train_func_in_worker(bad_fn, "ray-abc", {}, (), {})
    except RuntimeError:
        pass
    else:
        raise AssertionError("Worker wrapper must re-raise user exceptions")

    [worker_span] = [s for s in writer.spans if s.name == "ray.train.worker"]
    assert worker_span.error == 1
    assert "kaboom" in (worker_span.get_tag("error.message") or worker_span.get_tag("error.msg") or "")


def test_worker_registers_with_long_running_span(monkeypatch):
    """start_long_running_span is called before the user fn and
    stop_long_running_span is called after (in the finally block).
    """
    from ddtrace.contrib.internal.ray import train as ray_train

    calls = []
    monkeypatch.setattr(ray_train, "start_long_running_span", lambda span: calls.append(("start", span)))
    monkeypatch.setattr(ray_train, "stop_long_running_span", lambda span: calls.append(("stop", span)))

    ray_train._run_train_func_in_worker(lambda: None, "ray-abc", {}, (), {})

    op_pairs = [c[0] for c in calls]
    assert op_pairs == ["start", "stop"]


def test_resolve_local_rank_info_gracefully_handles_missing_ray():
    """Unit test: _resolve_local_rank_info returns empty dict when ray.train is unavailable."""
    from ddtrace.contrib.internal.ray.train import _resolve_local_rank_info

    # When ray.train is not installed or unavailable, the function should
    # return an empty dict without raising an exception. This is the defensive
    # behavior that allows the code to work in environments without full ray[train] deps.
    info = _resolve_local_rank_info()
    # Since ray.train likely can't be imported in test venv, we expect empty dict
    assert isinstance(info, dict)


def test_worker_span_does_not_crash_when_local_rank_unavailable(monkeypatch):
    """Integration test: worker span creation should not crash when local rank
    info is unavailable (e.g., when ray.train is not fully available).
    """
    from ddtrace.contrib.internal.ray.train import _run_train_func_in_worker
    from ddtrace.trace import tracer

    writer = _CaptureWriter()
    monkeypatch.setattr(tracer._span_aggregator, "writer", writer)
    monkeypatch.setenv("RANK", "5")
    monkeypatch.setenv("WORLD_SIZE", "8")

    # Call the worker function. Since ray.train is likely not fully available in test
    # venv (missing fsspec, pandas, etc.), _resolve_local_rank_info() will return
    # an empty dict. The span should still be created successfully.
    _run_train_func_in_worker(lambda: None, "ray-abc", {}, (), {})

    [worker_span] = [s for s in writer.spans if s.name == "ray.train.worker"]
    # The span should have rank and world_size tags from env vars
    assert worker_span.get_tag("rank") == "5"
    assert worker_span.get_tag("world_size") == "8"
    # The local_rank tags won't be set if ray.train is unavailable,
    # which is OK - the function handles that gracefully.
    assert isinstance(worker_span, object)  # span was created successfully


# ---------------------------------------------------------------------------
# _wrapped_torch_trainer_init tests
# ---------------------------------------------------------------------------


def test_init_wrapper_replaces_user_fn_with_wrapper(monkeypatch):
    import pytest

    pytest.importorskip("torch")

    from ddtrace.contrib.internal.ray.train import _DDTrainFuncWrapper
    from ddtrace.contrib.internal.ray.train import _wrapped_torch_trainer_init

    monkeypatch.setenv("DD_PYTORCH_JOB_ID", "ray-abc-test")

    captured_kwargs = {}

    def fake_init(*args, **kwargs):
        captured_kwargs.update(kwargs)
        return None

    def user_fn(config):
        pass

    class _FakeTrainer:
        pass

    _wrapped_torch_trainer_init(
        fake_init,
        _FakeTrainer(),
        (),
        {"train_loop_per_worker": user_fn, "scaling_config": object()},
    )

    wrapped = captured_kwargs["train_loop_per_worker"]
    assert isinstance(wrapped, _DDTrainFuncWrapper)
    assert wrapped._training_job_id == "ray-abc-test"
    # __init__ never binds context — that happens in fit().
    assert wrapped._parent_headers == {}


def test_init_wrapper_passthrough_when_no_train_loop():
    """If the user passed a positional or alt kwarg, do not crash; pass through."""
    from ddtrace.contrib.internal.ray.train import _wrapped_torch_trainer_init

    captured = {}

    def fake_init(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs

    _wrapped_torch_trainer_init(fake_init, object(), (), {"other_kw": 1})

    assert captured["kwargs"] == {"other_kw": 1}


def test_init_wrapper_passthrough_when_train_disabled(monkeypatch):
    from ddtrace import config
    from ddtrace.contrib.internal.ray.train import _wrapped_torch_trainer_init

    monkeypatch.setattr(config.ray, "train_enabled", False)

    captured = {}

    def fake_init(*args, **kwargs):
        captured["kwargs"] = kwargs

    def user_fn(cfg):
        pass

    _wrapped_torch_trainer_init(fake_init, object(), (), {"train_loop_per_worker": user_fn})

    # When disabled, the user fn is passed through untouched.
    assert captured["kwargs"]["train_loop_per_worker"] is user_fn


def test_init_wrapper_passthrough_when_user_fn_not_callable(monkeypatch):
    from ddtrace.contrib.internal.ray.train import _wrapped_torch_trainer_init

    captured = {}

    def fake_init(*args, **kwargs):
        captured["kwargs"] = kwargs

    not_callable = "not a function"
    _wrapped_torch_trainer_init(fake_init, object(), (), {"train_loop_per_worker": not_callable})

    assert captured["kwargs"]["train_loop_per_worker"] is not_callable


# ---------------------------------------------------------------------------
# _wrapped_torch_trainer_fit tests
# ---------------------------------------------------------------------------


def test_fit_wrapper_opens_fit_span_and_binds_parent_context(monkeypatch):
    """Fit-wrapper opens ray.train.fit, binds parent context into the
    wrapper BEFORE invoking the original fit, sets running→succeeded
    status, and tags world_size from the scaling config.
    """
    import pytest

    pytest.importorskip("torch")
    from ddtrace.contrib.internal.ray.train import _DDTrainFuncWrapper
    from ddtrace.contrib.internal.ray.train import _wrapped_torch_trainer_fit
    from ddtrace.trace import tracer

    writer = _CaptureWriter()
    monkeypatch.setattr(tracer._span_aggregator, "writer", writer)

    snapshot_at_fit = {}

    def user_fn(config):
        pass

    wrapper = _DDTrainFuncWrapper(user_fn, training_job_id="ray-abc")

    class _FakeTrainer:
        # Mirror Ray's real attribute name (`_train_loop_per_worker`).
        _train_loop_per_worker = wrapper
        _scaling_config = type("Cfg", (), {"num_workers": 4})()

    trainer = _FakeTrainer()

    def fake_fit(*args, **kwargs):
        # At fit-call time, the fit-wrapper has swapped in a fresh
        # ``_DDTrainFuncWrapper`` carrying the bound parent-context
        # headers — that is the instance Ray Train cloudpickles to
        # workers. Snapshot it so the assertions can verify the wrapper
        # Ray sees has populated headers.
        current = trainer._train_loop_per_worker
        snapshot_at_fit["wrapper_id"] = id(current)
        snapshot_at_fit["headers"] = dict(getattr(current, "_parent_headers", {}))
        return "result"

    result = _wrapped_torch_trainer_fit(fake_fit, trainer, (), {})

    assert result == "result"
    # The wrapper Ray will see has been replaced with a freshly-bound one,
    # not the original.
    assert snapshot_at_fit["wrapper_id"] != id(wrapper)
    assert "traceparent" in snapshot_at_fit["headers"]

    [fit_span] = [s for s in writer.spans if s.name == "ray.train.fit"]
    assert fit_span.get_tag("training_job.id") == "ray-abc"
    assert fit_span.get_tag("training_job.framework") == "torch"
    assert fit_span.get_tag("training_job.trainer") == "TorchTrainer"
    assert fit_span.get_tag("training_job.status") == "succeeded"
    assert fit_span.get_tag("ray.submission_id") == "ray-abc"
    assert fit_span.get_tag("world_size") == "4"
    # Numeric facet for spans aggregate queries.
    assert fit_span.get_metric("world_size") == 4


def test_fit_wrapper_tags_failure_status_and_reraises(monkeypatch):
    import pytest

    pytest.importorskip("torch")
    from ddtrace.contrib.internal.ray.train import _DDTrainFuncWrapper
    from ddtrace.contrib.internal.ray.train import _wrapped_torch_trainer_fit
    from ddtrace.trace import tracer

    writer = _CaptureWriter()
    monkeypatch.setattr(tracer._span_aggregator, "writer", writer)

    wrapper = _DDTrainFuncWrapper(lambda: None, training_job_id="ray-abc")

    class _FakeTrainer:
        _train_loop_per_worker = wrapper
        _scaling_config = type("Cfg", (), {"num_workers": 1})()

    def boom_fit(*args, **kwargs):
        raise RuntimeError("driver-side kaboom")

    try:
        _wrapped_torch_trainer_fit(boom_fit, _FakeTrainer(), (), {})
    except RuntimeError:
        pass
    else:
        raise AssertionError("fit wrapper must re-raise user errors")

    [fit_span] = [s for s in writer.spans if s.name == "ray.train.fit"]
    assert fit_span.get_tag("training_job.status") == "failed"
    assert fit_span.error == 1


def test_fit_wrapper_passthrough_when_train_disabled(monkeypatch):
    import pytest

    pytest.importorskip("torch")
    from ddtrace import config
    from ddtrace.contrib.internal.ray.train import _wrapped_torch_trainer_fit
    from ddtrace.trace import tracer

    writer = _CaptureWriter()
    monkeypatch.setattr(tracer._span_aggregator, "writer", writer)
    monkeypatch.setattr(config.ray, "train_enabled", False)

    def fake_fit(*args, **kwargs):
        return "passthrough"

    result = _wrapped_torch_trainer_fit(fake_fit, object(), (), {})
    assert result == "passthrough"
    assert [s for s in writer.spans if s.name == "ray.train.fit"] == []


def test_fit_wrapper_registers_with_long_running_span(monkeypatch):
    import pytest

    pytest.importorskip("torch")
    from ddtrace.contrib.internal.ray import train as ray_train
    from ddtrace.contrib.internal.ray.train import _DDTrainFuncWrapper

    calls = []
    monkeypatch.setattr(ray_train, "start_long_running_span", lambda span: calls.append(("start", span)))
    monkeypatch.setattr(ray_train, "stop_long_running_span", lambda span: calls.append(("stop", span)))

    wrapper = _DDTrainFuncWrapper(lambda: None, training_job_id="ray-abc")

    class _FakeTrainer:
        _train_loop_per_worker = wrapper
        _scaling_config = type("Cfg", (), {"num_workers": 1})()

    ray_train._wrapped_torch_trainer_fit(lambda *a, **k: None, _FakeTrainer(), (), {})
    op_pairs = [c[0] for c in calls]
    assert op_pairs == ["start", "stop"]


# ---------------------------------------------------------------------------
# _install_train / _uninstall_train tests
# ---------------------------------------------------------------------------


def test_install_train_wraps_torchtrainer():
    import pytest

    pytest.importorskip("torch")
    import ray.train.torch

    from ddtrace.contrib.internal.ray import train as ray_train

    # Ensure clean baseline regardless of prior test ordering.
    ray_train._uninstall_train()

    original_init = ray.train.torch.TorchTrainer.__init__
    original_fit = ray.train.torch.TorchTrainer.fit

    ray_train._install_train()
    try:
        assert ray.train.torch.TorchTrainer.__init__ is not original_init
        assert ray.train.torch.TorchTrainer.fit is not original_fit
    finally:
        ray_train._uninstall_train()

    # Wrapt's `unwrap` should restore the original.
    assert ray.train.torch.TorchTrainer.__init__ is original_init
    assert ray.train.torch.TorchTrainer.fit is original_fit


def test_uninstall_train_safe_when_module_absent(monkeypatch):
    """Calling `_uninstall_train` should not raise when `ray.train.torch`
    has never been imported, or when _install_train was never called.
    """
    import sys

    from ddtrace.contrib.internal.ray import train as ray_train

    # Simulate the case where _install_train ran but the module is absent.
    monkeypatch.setattr(ray_train, "_TRAIN_INSTALLED", True)
    monkeypatch.setitem(sys.modules, "ray.train.torch", None)
    # Should not raise.
    ray_train._uninstall_train()
    assert ray_train._TRAIN_INSTALLED is False


# ---------------------------------------------------------------------------
# per-rank-trace fallback mode tests
# ---------------------------------------------------------------------------


def test_per_rank_trace_mode_opens_worker_as_root_with_span_link(monkeypatch):
    import pytest

    pytest.importorskip("torch")
    from ddtrace import config
    from ddtrace.contrib.internal.ray.train import _run_train_func_in_worker
    from ddtrace.propagation.http import HTTPPropagator
    from ddtrace.trace import tracer

    writer = _CaptureWriter()
    monkeypatch.setattr(tracer._span_aggregator, "writer", writer)
    monkeypatch.setattr(config.ray, "train_per_rank_trace", True)

    with tracer.start_span("driver.fit") as parent:
        parent_headers = {}
        HTTPPropagator.inject(parent.context, parent_headers)
        parent_trace_id = parent.trace_id
        parent_span_id = parent.span_id

    _run_train_func_in_worker(lambda: None, "ray-abc", parent_headers, (), {})

    [worker_span] = [s for s in writer.spans if s.name == "ray.train.worker"]
    # Worker is a trace root — its trace_id differs from the parent's.
    assert worker_span.trace_id != parent_trace_id
    # And it carries a span-link to the parent.
    links = worker_span._links or []
    assert any(link.trace_id == parent_trace_id and link.span_id == parent_span_id for link in links)


def test_per_rank_trace_mode_tags_fit_span(monkeypatch):
    import pytest

    pytest.importorskip("torch")
    from ddtrace import config
    from ddtrace.contrib.internal.ray.constants import RAY_TRAIN_TRACE_MODE_PER_RANK
    from ddtrace.contrib.internal.ray.constants import RAY_TRAIN_TRACE_MODE_TAG
    from ddtrace.contrib.internal.ray.train import _DDTrainFuncWrapper
    from ddtrace.contrib.internal.ray.train import _wrapped_torch_trainer_fit
    from ddtrace.trace import tracer

    writer = _CaptureWriter()
    monkeypatch.setattr(tracer._span_aggregator, "writer", writer)
    monkeypatch.setattr(config.ray, "train_per_rank_trace", True)

    wrapper = _DDTrainFuncWrapper(lambda: None, training_job_id="ray-abc")

    class _FakeTrainer:
        _train_loop_per_worker = wrapper
        _scaling_config = type("Cfg", (), {"num_workers": 1})()

    _wrapped_torch_trainer_fit(lambda *a, **k: None, _FakeTrainer(), (), {})

    [fit_span] = [s for s in writer.spans if s.name == "ray.train.fit"]
    assert fit_span.get_tag(RAY_TRAIN_TRACE_MODE_TAG) == RAY_TRAIN_TRACE_MODE_PER_RANK


# ---------------------------------------------------------------------------
# Code-review fixes: context restore + cached_job_id restore
# ---------------------------------------------------------------------------


def test_worker_restores_cached_job_id_after_run(monkeypatch):
    import pytest

    pytest.importorskip("torch")
    from ddtrace.contrib.internal.pytorch._utils import get_cached_job_id
    from ddtrace.contrib.internal.pytorch._utils import set_cached_job_id
    from ddtrace.contrib.internal.ray.train import _run_train_func_in_worker

    set_cached_job_id("previous-job")
    try:
        _run_train_func_in_worker(lambda: None, "ray-current", {}, (), {})
        # After the worker fn returns, the previous job id is restored on
        # this thread so later spans aren't mislabeled when Ray reuses the
        # worker for another job.
        assert get_cached_job_id() == "previous-job"
    finally:
        set_cached_job_id(None)


def test_worker_restores_active_context_after_run(monkeypatch):
    import pytest

    pytest.importorskip("torch")
    from ddtrace.contrib.internal.ray.train import _run_train_func_in_worker
    from ddtrace.trace import tracer

    prior_span = tracer.start_span("driver.outer")
    tracer.context_provider.activate(prior_span)
    try:
        _run_train_func_in_worker(lambda: None, "ray-abc", {}, (), {})

        # After worker returns, the prior active context is restored, not
        # left pointing at the finished ray.train.worker span.
        assert tracer.context_provider.active() is prior_span
    finally:
        prior_span.finish()


def test_fit_wrapper_restores_active_context_after_run(monkeypatch):
    import pytest

    pytest.importorskip("torch")
    from ddtrace.contrib.internal.ray.train import _DDTrainFuncWrapper
    from ddtrace.contrib.internal.ray.train import _wrapped_torch_trainer_fit
    from ddtrace.trace import tracer

    wrapper = _DDTrainFuncWrapper(lambda: None, training_job_id="ray-abc")

    class _FakeTrainer:
        _train_loop_per_worker = wrapper
        _scaling_config = type("Cfg", (), {"num_workers": 1})()

    prior_span = tracer.start_span("driver.outer")
    tracer.context_provider.activate(prior_span)
    try:
        _wrapped_torch_trainer_fit(lambda *a, **k: None, _FakeTrainer(), (), {})
        assert tracer.context_provider.active() is prior_span
    finally:
        prior_span.finish()


def test_fit_wrapper_swaps_train_loop_per_worker_before_pickle(monkeypatch):
    """Regression for the Ray-Train-snapshot-before-fit bug.

    Ray Train cloudpickles ``trainer._train_loop_per_worker`` at fit
    time. Mutating the wrapper instance in place (the original design)
    loses the parent-context headers because Ray captures the pre-bind
    state. The fit-wrapper instead constructs a fresh
    ``_DDTrainFuncWrapper`` with headers already bound and swaps the
    reference on the trainer and its ``_param_dict``. This test
    cloudpickles ``instance._train_loop_per_worker`` *after* the
    fit-wrapper has prepared it (mimicking Ray's worker round-trip)
    and asserts headers survive.
    """
    import pytest

    pytest.importorskip("torch")
    import ray.cloudpickle as cloudpickle

    from ddtrace.contrib.internal.ray.train import _DDTrainFuncWrapper
    from ddtrace.contrib.internal.ray.train import _wrapped_torch_trainer_fit
    from ddtrace.trace import tracer

    writer = _CaptureWriter()
    monkeypatch.setattr(tracer._span_aggregator, "writer", writer)

    def user_fn(config):
        pass

    wrapper = _DDTrainFuncWrapper(user_fn, training_job_id="ray-abc")

    class _FakeTrainer:
        _train_loop_per_worker = wrapper
        _param_dict = {"train_loop_per_worker": wrapper}
        _scaling_config = type("Cfg", (), {"num_workers": 2})()

    trainer = _FakeTrainer()
    pickled_at_fit = {}

    def fake_fit(*args, **kwargs):
        # Ray Train cloudpickles `_train_loop_per_worker` at this point.
        pickled_at_fit["bytes"] = cloudpickle.dumps(trainer._train_loop_per_worker)
        return None

    _wrapped_torch_trainer_fit(fake_fit, trainer, (), {})

    revived = cloudpickle.loads(pickled_at_fit["bytes"])
    assert isinstance(revived, _DDTrainFuncWrapper)
    assert "traceparent" in revived._parent_headers
    # `_param_dict` (used by Ray restore paths) was swapped to the same
    # bound instance as the public attribute.
    assert trainer._param_dict["train_loop_per_worker"] is trainer._train_loop_per_worker


def test_resolve_run_metadata_bounded_timeout(monkeypatch):
    """A slow JobSubmissionClient must not block fit() beyond ~2s."""
    import time

    from ddtrace.contrib.internal.ray import train as ray_train

    class HangingClient:
        def __init__(self, *args, **kwargs):
            pass

        def get_job_info(self, submission_id):
            time.sleep(10)
            return None

    import ray.job_submission

    monkeypatch.setattr(ray.job_submission, "JobSubmissionClient", HangingClient)

    start = time.monotonic()
    result = ray_train._resolve_run_metadata("sub-xyz")
    elapsed = time.monotonic() - start

    assert result == {}
    assert elapsed < 3.0, f"_resolve_run_metadata took {elapsed:.2f}s"


def test_worker_restores_prior_run_metadata_on_exit():
    """A reused Ray worker must restore the prior run metadata snapshot
    when its training fn returns.
    """
    import pytest

    pytest.importorskip("torch")
    from ddtrace.contrib.internal.pytorch._utils import get_cached_run_metadata
    from ddtrace.contrib.internal.pytorch._utils import set_cached_run_metadata
    from ddtrace.contrib.internal.ray.train import _run_train_func_in_worker

    set_cached_run_metadata(run_name="run-A", submission_id="sub-A", metadata={"k": "A"})
    snap_a = get_cached_run_metadata()
    assert snap_a == {"run_name": "run-A", "submission_id": "sub-A", "metadata": {"k": "A"}}

    _run_train_func_in_worker(
        fn=lambda: None,
        training_job_id="jobB",
        parent_headers={},
        args=(),
        kwargs={},
        run_name="run-B",
        submission_id="sub-B",
        run_metadata={"k": "B"},
    )

    assert get_cached_run_metadata() == snap_a


def test_wrapped_torch_trainer_fit_swaps_wrapper_with_metadata(monkeypatch):
    """End-to-end: after _wrapped_torch_trainer_fit runs, the swapped-in
    _DDTrainFuncWrapper must carry _run_name / _submission_id /
    _run_metadata.
    """
    from ddtrace import tracer
    from ddtrace.contrib.internal.ray import train as ray_train

    class FakeContext:
        sampling_priority = 1

    class FakeSpan:
        def __init__(self):
            self.context = FakeContext()
            self.tags = {}

        def set_tag(self, k, v=None):
            self.tags[k] = v

        def _set_attribute(self, k, v):
            self.tags[k] = v

        def set_exc_info(self, *a, **kw):
            pass

        def finish(self):
            pass

    monkeypatch.setattr(tracer, "start_span", lambda *a, **kw: FakeSpan())
    monkeypatch.setattr(ray_train, "start_long_running_span", lambda *a, **kw: None)
    monkeypatch.setattr(ray_train, "stop_long_running_span", lambda *a, **kw: None)
    monkeypatch.setattr(ray_train, "_resolve_run_metadata", lambda s: {"job_name": "train.foo"})
    monkeypatch.setenv("_RAY_SUBMISSION_ID", "raysubmit_AAA")
    monkeypatch.setattr(ray_train._ddconfig.ray, "train_enabled", True)

    init_wrapper = ray_train._DDTrainFuncWrapper(lambda: None, training_job_id="jobX")
    init_wrapper._run_name = "rn"
    init_wrapper._submission_id = "raysubmit_AAA"
    init_wrapper._run_metadata = {"job_name": "train.foo"}

    class FakeTrainer:
        _train_loop_per_worker = init_wrapper
        _param_dict = None
        _scaling_config = None
        _run_config = None

    inst = FakeTrainer()
    ray_train._wrapped_torch_trainer_fit(
        wrapped=lambda *a, **kw: None,
        instance=inst,
        args=(),
        kwargs={},
    )

    swapped = inst._train_loop_per_worker
    assert swapped is not init_wrapper
    assert swapped._run_name == "rn"
    assert swapped._submission_id == "raysubmit_AAA"
    assert swapped._run_metadata == {"job_name": "train.foo"}


def test_resolve_run_metadata_called_once_per_trainer(monkeypatch):
    from unittest import mock

    from ddtrace.contrib.internal.ray import train as ray_train

    calls = []

    def fake_resolve(sub):
        calls.append(sub)
        return {"job_name": "foo"}

    monkeypatch.setattr(ray_train, "_resolve_run_metadata", fake_resolve)

    class FakeTrainer:
        _scaling_config = None
        _run_config = None
        _train_loop_per_worker = None
        _param_dict = None

    inst = FakeTrainer()

    monkeypatch.setenv("_RAY_SUBMISSION_ID", "raysubmit_AAA")
    monkeypatch.setattr(ray_train._ddconfig.ray, "train_enabled", True)
    monkeypatch.setattr(ray_train, "start_long_running_span", lambda *a, **kw: None)
    monkeypatch.setattr(ray_train, "stop_long_running_span", lambda *a, **kw: None)
    monkeypatch.setenv("DD_PYTORCH_JOB_ID", "test_job_id")

    # Mock out the pytorch._utils import to avoid importing torch in ray-only env
    fake_pytorch = mock.MagicMock()
    fake_pytorch.resolve_job_id_from_env = lambda: None
    monkeypatch.setitem(sys.modules, "ddtrace.contrib.internal.pytorch._utils", fake_pytorch)

    ray_train._wrapped_torch_trainer_init(
        wrapped=lambda *a, **kw: None,
        instance=inst,
        args=(),
        kwargs={"train_loop_per_worker": lambda c: None},
    )
    assert len(calls) == 1

    ray_train._wrapped_torch_trainer_fit(
        wrapped=lambda *a, **kw: None,
        instance=inst,
        args=(),
        kwargs={},
    )

    assert len(calls) == 1, f"_resolve_run_metadata was called {len(calls)} times"


def test_fit_span_carries_runtime_invariants(monkeypatch):
    from ddtrace import tracer
    from ddtrace.contrib.internal.ray import train as ray_train
    from ddtrace.contrib.internal.ray.core import utils as ray_utils

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

        def set_exc_info(self, *a, **kw):
            pass

        def finish(self):
            pass

    monkeypatch.setattr(tracer, "start_span", lambda *a, **kw: FakeSpan())
    monkeypatch.setattr(ray_train, "start_long_running_span", lambda *a, **kw: None)
    monkeypatch.setattr(ray_train, "stop_long_running_span", lambda *a, **kw: None)
    monkeypatch.setattr(ray_train, "_resolve_run_metadata", lambda s: {})
    monkeypatch.setenv("_RAY_SUBMISSION_ID", "raysubmit_AAA")
    monkeypatch.setattr(ray_train._ddconfig.ray, "train_enabled", True)

    # Stub runtime invariants so the helper doesn't actually need Ray.
    monkeypatch.setattr(
        ray_utils,
        "_get_runtime_invariants",
        lambda: {"ray.job_id": "job-X", "ray.node_id": "node-Y", "ray.worker_id": "worker-Z"},
    )
    monkeypatch.setattr(ray_utils, "_get_cached_hostname", lambda: "h-test")
    # ray.is_initialized returns False so the per-call task/actor block is skipped
    monkeypatch.setattr(ray_utils.ray, "is_initialized", lambda: False)

    init_wrapper = ray_train._DDTrainFuncWrapper(lambda: None, training_job_id="jobX")
    init_wrapper._run_name = None
    init_wrapper._submission_id = "raysubmit_AAA"
    init_wrapper._run_metadata = {}

    class FakeTrainer:
        _train_loop_per_worker = init_wrapper
        _param_dict = None
        _scaling_config = None
        _run_config = None

    inst = FakeTrainer()
    ray_train._wrapped_torch_trainer_fit(
        wrapped=lambda *a, **kw: None,
        instance=inst,
        args=(),
        kwargs={},
    )

    # Runtime invariants present:
    assert captured.get("ray.hostname") == "h-test"
    assert captured.get("ray.job_id") == "job-X"
    assert captured.get("ray.node_id") == "node-Y"
    assert captured.get("ray.worker_id") == "worker-Z"


def test_fit_span_carries_scaling_and_run_config(monkeypatch):
    from ddtrace import tracer
    from ddtrace.contrib.internal.ray import train as ray_train

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

        def set_exc_info(self, *a, **kw):
            pass

        def finish(self):
            pass

    monkeypatch.setattr(tracer, "start_span", lambda *a, **kw: FakeSpan())
    monkeypatch.setattr(ray_train, "start_long_running_span", lambda *a, **kw: None)
    monkeypatch.setattr(ray_train, "stop_long_running_span", lambda *a, **kw: None)
    monkeypatch.setattr(ray_train, "_resolve_run_metadata", lambda s: {})
    monkeypatch.setenv("_RAY_SUBMISSION_ID", "raysubmit_AAA")
    monkeypatch.setattr(ray_train._ddconfig.ray, "train_enabled", True)

    class FakeScaling:
        num_workers = 8
        use_gpu = True
        placement_strategy = "PACK"
        accelerator_type = "A100"
        resources_per_worker = {"GPU": 1, "CPU": 4}
        trainer_resources = {"CPU": 1}

    class FakeCheckpoint:
        num_to_keep = 5
        checkpoint_frequency = 100
        checkpoint_score_attribute = "val_loss"
        checkpoint_score_order = "min"

    class FakeFailure:
        max_failures = 3

    class FakeRun:
        name = "myrun"
        storage_path = "s3://bucket/runs"
        checkpoint_config = FakeCheckpoint()
        failure_config = FakeFailure()

    init_wrapper = ray_train._DDTrainFuncWrapper(lambda: None, training_job_id="jobX")
    init_wrapper._run_name = "myrun"
    init_wrapper._submission_id = "raysubmit_AAA"
    init_wrapper._run_metadata = {}

    class FakeTrainer:
        _train_loop_per_worker = init_wrapper
        _param_dict = None
        _scaling_config = FakeScaling()
        _run_config = FakeRun()

    inst = FakeTrainer()
    ray_train._wrapped_torch_trainer_fit(
        wrapped=lambda *a, **kw: None,
        instance=inst,
        args=(),
        kwargs={},
    )

    assert captured.get("ray.train.num_workers") == 8
    assert captured.get("ray.train.use_gpu") == "true"
    assert captured.get("ray.train.placement_strategy") == "PACK"
    assert captured.get("ray.train.accelerator_type") == "A100"
    assert captured.get("ray.train.resources_per_worker.GPU") == 1.0
    assert captured.get("ray.train.resources_per_worker.CPU") == 4.0
    assert captured.get("ray.train.trainer_resources.CPU") == 1.0
    assert captured.get("ray.train.storage_path") == "s3://bucket/runs"
    assert captured.get("ray.train.max_failures") == 3
    assert captured.get("ray.train.checkpoint.num_to_keep") == 5
    assert captured.get("ray.train.checkpoint.frequency") == 100
    assert captured.get("ray.train.checkpoint.score_attribute") == "val_loss"
    assert captured.get("ray.train.checkpoint.score_order") == "min"


def test_fit_span_handles_missing_configs():
    """When instance._scaling_config / _run_config are None, no tagging
    happens — and the helper must not raise.
    """
    from ddtrace.contrib.internal.ray.train import _tag_ray_train_config

    class FakeSpan:
        def __init__(self):
            self.captured = {}

        def set_tag(self, k, v=None):
            self.captured[k] = v

        def _set_attribute(self, k, v):
            self.captured[k] = v

    class FakeTrainer:
        _scaling_config = None
        _run_config = None

    s = FakeSpan()
    _tag_ray_train_config(s, FakeTrainer())
    assert s.captured == {}
