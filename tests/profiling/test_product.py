from contextlib import contextmanager
import sys
import time
from typing import Any
from typing import Callable
from typing import Iterator
from unittest.mock import Mock
from unittest.mock import patch

import pytest

from ddtrace.internal import _unpatched
from ddtrace.internal import forksafe
from ddtrace.internal import profiling_product as _product
from ddtrace.internal.products import ProductManager
from ddtrace.internal.service import ServiceStatus
from ddtrace.internal.settings._config import config as global_config


@pytest.fixture(autouse=True)
def reset_product_state():
    _product._lock = forksafe.ResetObject(_unpatched.threading_RLock)
    _product._request_lock = forksafe.Lock()
    _product._product_state = _product._PRODUCT_RUNNING
    _product._desired_requested = None
    _product._admission_open = True
    _product._fork_in_progress = False
    _product._fork_lock_holders = {}
    _product._exit_signal_registered = False
    _product._lifecycle_worker = None
    _product._profiler = None
    _product._cleanup_pending = False
    _product._partial_start_cleanup_pending = False
    with patch.object(global_config, "_remote_config_enabled", True):
        yield

    worker = _product._lifecycle_worker
    if worker is not None and worker.status == ServiceStatus.RUNNING:
        worker.stop()
        worker.join()

    _product._lock = forksafe.ResetObject(_unpatched.threading_RLock)
    _product._request_lock = forksafe.Lock()
    _product._product_state = _product._PRODUCT_INITIALIZING
    _product._desired_requested = None
    _product._admission_open = True
    _product._fork_in_progress = False
    _product._fork_lock_holders = {}
    _product._exit_signal_registered = False
    _product._lifecycle_worker = None
    _product._profiler = None
    _product._cleanup_pending = False
    _product._partial_start_cleanup_pending = False


def make_profiler() -> Mock:
    profiler = Mock()
    profiler.status = ServiceStatus.STOPPED
    profiler.start.side_effect = lambda: setattr(profiler, "status", ServiceStatus.RUNNING)
    profiler._start_with_active_lock.side_effect = lambda **_: profiler.start()
    profiler.stop.side_effect = lambda **_: setattr(profiler, "status", ServiceStatus.STOPPED)
    profiler._stop_with_active_lock.side_effect = profiler.stop
    profiler._rollback_start_with_active_lock.side_effect = lambda: setattr(profiler, "status", ServiceStatus.STOPPED)
    return profiler


@contextmanager
def patch_profiler(**kwargs: Any) -> Iterator[Mock]:
    profiler_type = Mock(**kwargs)
    profiler_type._active_instance = None
    profiler_type._active_lock = _unpatched.threading_RLock()
    with patch("ddtrace.profiling.profiler.Profiler", profiler_type):
        yield profiler_type


def apply_remote_config(lib_config: dict[str, object], _config: object = None) -> None:
    _product.apm_tracing_rc(lib_config, _config)
    _product._reconcile_requested()


def wait_until(predicate: Callable[[], bool]) -> None:
    deadline = time.monotonic() + 5
    while not predicate():
        assert time.monotonic() < deadline
        time.sleep(0.01)


def test_enabled_reflects_private_setting() -> None:
    with (
        patch.object(_product.profiling_config, "enabled", False),
        patch.object(_product.profiling_config, "remote_config_poc_enabled", True),
    ):
        assert _product.enabled() is True


def test_enabled_is_false_when_automatic_profiling_is_enabled() -> None:
    with (
        patch.object(_product.profiling_config, "enabled", True),
        patch.object(_product.profiling_config, "remote_config_poc_enabled", True),
    ):
        assert _product.enabled() is False


def test_product_does_not_start_when_remote_config_is_disabled() -> None:
    manager = ProductManager()
    manager._products = [("profiling-rc-poc", _product)]
    _product._product_state = _product._PRODUCT_INITIALIZING

    with (
        patch.object(global_config, "_remote_config_enabled", False),
        patch.object(_product.profiling_config, "enabled", False),
        patch.object(_product.profiling_config, "remote_config_poc_enabled", True),
        patch.object(_product, "_LifecycleWorker") as worker_type,
        patch.object(_product.atexit, "register_on_exit_signal") as register_exit_signal,
    ):
        manager.start_products()

    worker_type.assert_not_called()
    register_exit_signal.assert_not_called()


def test_product_module_does_not_eagerly_import_profiling_package() -> None:
    import subprocess

    script = "\n".join(
        [
            "import sys",
            "import ddtrace.internal.profiling_product",
            "assert 'ddtrace.profiling' not in sys.modules",
            "assert 'ddtrace.profiling.profiler' not in sys.modules",
        ]
    )

    subprocess.run([sys.executable, "-c", script], check=True)


def test_callback_ignores_sampling_rules_when_automatic_profiling_is_enabled() -> None:
    with (
        patch.object(_product.profiling_config, "enabled", True),
        patch.object(_product.profiling_config, "remote_config_poc_enabled", True),
        patch_profiler() as profiler_type,
    ):
        apply_remote_config({"tracing_sampling_rules": [{"sample_rate": 1.0}]}, None)

    profiler_type.assert_not_called()
    assert _product._profiler is None


def test_nonempty_sampling_rules_start_one_profiler() -> None:
    profiler = make_profiler()
    with (
        patch.object(_product.profiling_config, "remote_config_poc_enabled", True),
        patch_profiler(return_value=profiler) as profiler_type,
    ):
        apply_remote_config({"tracing_sampling_rules": [{"sample_rate": 1.0}]}, None)
        apply_remote_config({"tracing_sampling_rules": [{"sample_rate": 0.5}]}, None)

    profiler_type.assert_called_once_with()
    profiler.start.assert_called_once_with()
    profiler._start_with_active_lock.assert_called_once_with(register_on_exit_signal=False)
    assert _product._profiler is profiler


@pytest.mark.parametrize(
    "lib_config",
    [{}, {"tracing_sampling_rules": []}, {"exception_replay_enabled": True}],
    ids=["absent", "empty", "unrelated"],
)
def test_config_without_nonempty_sampling_rules_stops_and_flushes_owned_profiler(
    lib_config: dict[str, object],
) -> None:
    profiler = make_profiler()
    profiler.start()
    _product._profiler = profiler

    with patch.object(_product.profiling_config, "remote_config_poc_enabled", True):
        apply_remote_config(lib_config, None)

    profiler.stop.assert_called_once_with(flush=True)
    assert _product._profiler is None


def test_second_enable_constructs_fresh_profiler() -> None:
    first = make_profiler()
    second = make_profiler()
    with (
        patch.object(_product.profiling_config, "remote_config_poc_enabled", True),
        patch_profiler(side_effect=[first, second]) as profiler_type,
    ):
        apply_remote_config({"tracing_sampling_rules": [{"sample_rate": 1.0}]}, None)
        apply_remote_config({"exception_replay_enabled": True}, None)
        apply_remote_config({"tracing_sampling_rules": [{"sample_rate": 1.0}]}, None)

    assert profiler_type.call_count == 2
    assert _product._profiler is second


def test_disabled_flag_ignores_sampling_rules() -> None:
    with (
        patch.object(_product.profiling_config, "remote_config_poc_enabled", False),
        patch_profiler() as profiler_type,
    ):
        apply_remote_config({"tracing_sampling_rules": [{"sample_rate": 1.0}]}, None)

    profiler_type.assert_not_called()


def test_initial_config_without_sampling_rules_is_noop() -> None:
    with (
        patch.object(_product.profiling_config, "remote_config_poc_enabled", True),
        patch_profiler() as profiler_type,
    ):
        apply_remote_config({}, None)

    profiler_type.assert_not_called()


def test_unrelated_nonempty_config_is_noop() -> None:
    profiler = make_profiler()
    with (
        patch.object(_product.profiling_config, "remote_config_poc_enabled", True),
        patch_profiler(return_value=profiler),
    ):
        apply_remote_config({"exception_replay_enabled": True}, None)

    assert _product._profiler is None


def test_callback_returns_while_lifecycle_worker_is_blocked() -> None:
    import threading

    profiler = make_profiler()
    entered = threading.Event()
    release = threading.Event()

    def block_start() -> None:
        entered.set()
        assert release.wait(timeout=5)
        profiler.status = ServiceStatus.RUNNING

    profiler.start.side_effect = block_start
    _product._product_state = _product._PRODUCT_INITIALIZING
    callback_returned = threading.Event()

    def invoke_callback() -> None:
        try:
            _product.apm_tracing_rc({"tracing_sampling_rules": [{"sample_rate": 1.0}]}, None)
        finally:
            callback_returned.set()

    with (
        patch.object(_product.profiling_config, "remote_config_poc_enabled", True),
        patch_profiler(return_value=profiler),
        patch.object(_product.atexit, "register_on_exit_signal"),
    ):
        _product.start()
        callback = threading.Thread(target=invoke_callback)
        callback.start()
        try:
            assert callback_returned.wait(timeout=0.5)
            assert entered.wait(timeout=5)
            assert _product._desired_requested is True
        finally:
            release.set()
            callback.join(timeout=5)

        assert not callback.is_alive()
        wait_until(lambda: _product._profiler is profiler)


def test_product_start_rolls_back_partially_started_lifecycle_worker() -> None:
    native_worker = Mock()
    native_worker.start.side_effect = KeyboardInterrupt
    native_worker._cancel_deferred_start_unlocked.return_value = False
    _product._product_state = _product._PRODUCT_INITIALIZING

    with (
        patch.object(_product.atexit, "register_on_exit_signal"),
        patch.object(_product.periodic, "PeriodicThread", return_value=native_worker),
        pytest.raises(KeyboardInterrupt),
    ):
        _product.start()

    native_worker._cancel_deferred_start_unlocked.assert_called_once_with()
    native_worker.stop.assert_called_once_with()
    native_worker.join.assert_called_once_with(None)
    assert _product._lifecycle_worker is None
    assert _product._product_state == _product._PRODUCT_STOPPED
    assert _product._admission_open is False


def test_product_start_joins_failed_worker_after_releasing_product_lock() -> None:
    import threading

    transition_started = threading.Event()

    class InterruptingWorker:
        status = ServiceStatus.STOPPED

        def __init__(self) -> None:
            self.join_completed = False
            self.thread = threading.Thread(target=self.transition)

        def transition(self) -> None:
            transition_started.set()
            with _product._lock:
                pass

        def start(self) -> None:
            self.thread.start()
            assert transition_started.wait(timeout=5)
            raise KeyboardInterrupt

        def _rollback_start(self) -> None:
            pass

        def join(self) -> None:
            self.thread.join(timeout=0.2)
            self.join_completed = not self.thread.is_alive()
            if not self.join_completed:
                raise RuntimeError("worker is blocked on the product lock")

    worker = InterruptingWorker()
    _product._product_state = _product._PRODUCT_INITIALIZING

    with (
        patch.object(_product.atexit, "register_on_exit_signal"),
        patch.object(_product, "_LifecycleWorker", return_value=worker),
        pytest.raises(KeyboardInterrupt),
    ):
        _product.start()

    worker.thread.join(timeout=5)
    assert not worker.thread.is_alive()
    assert worker.join_completed


def test_callback_coalesces_to_latest_requested_state() -> None:
    with (
        patch.object(_product.profiling_config, "remote_config_poc_enabled", True),
        patch_profiler() as profiler_type,
    ):
        _product.apm_tracing_rc({"tracing_sampling_rules": [{"sample_rate": 1.0}]}, None)
        _product.apm_tracing_rc({}, None)
        _product._reconcile_requested()

    profiler_type.assert_not_called()
    assert _product._desired_requested is False


def test_lifecycle_worker_retries_requested_enable_without_new_config() -> None:
    replacement = make_profiler()

    with (
        patch.object(_product.profiling_config, "remote_config_poc_enabled", True),
        patch_profiler(side_effect=[RuntimeError("start failed"), replacement]) as profiler_type,
    ):
        _product.apm_tracing_rc({"tracing_sampling_rules": [{"sample_rate": 1.0}]}, None)
        _product._reconcile_requested()
        assert _product._profiler is None

        _product._reconcile_requested()

    assert profiler_type.call_count == 2
    assert _product._profiler is replacement


def test_lifecycle_worker_retries_when_application_profiler_stops() -> None:
    application = Mock(status=ServiceStatus.RUNNING)
    replacement = make_profiler()
    with (
        patch.object(_product.profiling_config, "remote_config_poc_enabled", True),
        patch_profiler(return_value=replacement) as profiler_type,
    ):
        profiler_type._active_instance = application
        _product.apm_tracing_rc({"tracing_sampling_rules": [{"sample_rate": 1.0}]}, None)
        _product._reconcile_requested()
        profiler_type.assert_not_called()

        profiler_type._active_instance = None
        _product._reconcile_requested()

    profiler_type.assert_called_once_with()
    assert _product._profiler is replacement


def test_product_stop_stops_and_joins_lifecycle_worker() -> None:
    worker = Mock(status=ServiceStatus.RUNNING)
    _product._lifecycle_worker = worker

    _product.stop(join=True)

    worker.stop.assert_called_once_with()
    worker.join.assert_called_once_with()
    assert _product._lifecycle_worker is None


def test_product_stop_flushes_owned_profiler() -> None:
    profiler = make_profiler()
    profiler.start()
    _product._profiler = profiler

    _product.stop(join=True)

    profiler.stop.assert_called_once_with(flush=True)
    assert _product._profiler is None


def test_product_stop_closes_admission_to_queued_enable() -> None:
    import threading

    callback = threading.Thread(
        target=_product.apm_tracing_rc,
        args=({"tracing_sampling_rules": [{"sample_rate": 1.0}]}, None),
    )

    with patch_profiler() as profiler_type:
        _product._lock.acquire()
        try:
            callback.start()
            _product.stop()
        finally:
            _product._lock.release()
        callback.join(timeout=5)

    assert not callback.is_alive()
    profiler_type.assert_not_called()
    assert _product._product_state == _product._PRODUCT_STOPPED


def test_product_start_does_not_reopen_admission_to_queued_enable() -> None:
    import threading

    callback = threading.Thread(
        target=_product.apm_tracing_rc,
        args=({"tracing_sampling_rules": [{"sample_rate": 1.0}]}, None),
    )

    with patch_profiler() as profiler_type:
        _product._lock.acquire()
        try:
            callback.start()
            _product.stop()
            _product.start()
        finally:
            _product._lock.release()
        callback.join(timeout=5)

    assert not callback.is_alive()
    profiler_type.assert_not_called()
    assert _product._product_state == _product._PRODUCT_STOPPED


def test_dependency_config_before_product_start_is_replayed() -> None:
    profiler = make_profiler()
    _product._product_state = _product._PRODUCT_INITIALIZING

    with (
        patch.object(_product.profiling_config, "remote_config_poc_enabled", True),
        patch_profiler(return_value=profiler) as profiler_type,
        patch.object(_product.atexit, "register_on_exit_signal"),
    ):
        apply_remote_config({"tracing_sampling_rules": [{"sample_rate": 1.0}]}, None)

        profiler_type.assert_not_called()
        assert _product._desired_requested is True

        _product.start()
        wait_until(lambda: _product._profiler is profiler)

    profiler_type.assert_called_once_with()
    assert _product._profiler is profiler
    assert _product._desired_requested is True


def test_apm_product_registers_handler_before_enabling_remote_config() -> None:
    from ddtrace.internal.products import manager
    from ddtrace.internal.remoteconfig.products import apm_tracing
    from ddtrace.internal.remoteconfig.worker import remoteconfig_poller

    profiler = make_profiler()
    handlers: dict[str, Callable[[dict[str, object], object], None]] = {}
    events: list[str] = []
    _product._product_state = _product._PRODUCT_INITIALIZING

    def register_handler(
        _event: str,
        handler: Callable[[dict[str, object], object], None],
        name: str,
    ) -> None:
        handlers[name] = handler
        events.append("handler")

    def register_callback(*_args: object, **_kwargs: object) -> None:
        events.append("callback")

    def enable_product(_remote_config_product: object) -> None:
        events.append("enable")
        handlers["profiling-rc-poc"]({"tracing_sampling_rules": [{"sample_rate": 1.0}]}, None)

    with (
        patch.object(_product.profiling_config, "remote_config_poc_enabled", True),
        patch.object(manager, "__products__", {"profiling-rc-poc": _product}),
        patch.object(apm_tracing, "on", side_effect=register_handler),
        patch.object(remoteconfig_poller, "register_callback", side_effect=register_callback),
        patch.object(remoteconfig_poller, "enable_product", side_effect=enable_product),
        patch_profiler(return_value=profiler),
        patch.object(_product.atexit, "register_on_exit_signal"),
    ):
        apm_tracing.start()
        assert _product._desired_requested is True
        _product.start()
        wait_until(lambda: _product._profiler is profiler)

    assert events == ["handler", "callback", "enable"]
    assert _product._profiler is profiler


def test_signal_stop_skips_transition_owned_by_signal_thread() -> None:
    profiler = make_profiler()
    profiler.start()
    _product._profiler = profiler

    with _product._lock:
        _product._stop_on_signal()

    profiler._stop_on_signal.assert_not_called()
    assert _product._profiler is profiler


def test_product_stop_uses_public_stop_for_normally_stopped_owned_profiler() -> None:
    profiler = make_profiler()
    profiler.start()
    _product._profiler = profiler

    profiler.stop()
    profiler.stop.reset_mock()
    _product.stop()

    profiler.stop.assert_called_once_with(flush=True)
    profiler._rollback_start_with_active_lock.assert_not_called()
    assert _product._profiler is None


def test_constructor_failure_leaves_stopped_state() -> None:
    with (
        patch.object(_product.profiling_config, "remote_config_poc_enabled", True),
        patch_profiler(side_effect=RuntimeError("start failed")),
        patch.object(_product.log, "exception") as log_exception,
    ):
        apply_remote_config({"tracing_sampling_rules": [{"sample_rate": 1.0}]}, None)

    assert _product._profiler is None
    log_exception.assert_called_once()


def test_start_failure_rolls_back_running_profiler() -> None:
    profiler = make_profiler()

    def fail_after_start() -> None:
        profiler.status = ServiceStatus.RUNNING
        raise RuntimeError("start failed")

    profiler.start.side_effect = fail_after_start
    with (
        patch.object(_product.profiling_config, "remote_config_poc_enabled", True),
        patch_profiler(return_value=profiler),
    ):
        apply_remote_config({"tracing_sampling_rules": [{"sample_rate": 1.0}]}, None)

    profiler.stop.assert_called_once_with(flush=True)
    assert profiler.status == ServiceStatus.STOPPED
    assert _product._profiler is None
    assert _product._cleanup_pending is False
    assert _product._partial_start_cleanup_pending is False


def test_interrupted_start_restores_state_before_reraising() -> None:
    failed = make_profiler()
    replacement = make_profiler()
    failed._start_with_active_lock.side_effect = KeyboardInterrupt

    with (
        patch.object(_product.profiling_config, "remote_config_poc_enabled", True),
        patch_profiler(side_effect=[failed, replacement]),
    ):
        with pytest.raises(KeyboardInterrupt):
            apply_remote_config({"tracing_sampling_rules": [{"sample_rate": 1.0}]}, None)

        assert _product._profiler is None
        assert _product._cleanup_pending is False
        assert _product._partial_start_cleanup_pending is False
        apply_remote_config({"tracing_sampling_rules": [{"sample_rate": 0.5}]}, None)

    assert _product._profiler is replacement


def test_start_failure_retains_running_profiler_when_rollback_fails() -> None:
    profiler = make_profiler()

    def fail_after_start() -> None:
        profiler.status = ServiceStatus.RUNNING
        raise RuntimeError("start failed")

    profiler.start.side_effect = fail_after_start
    profiler.stop.side_effect = RuntimeError("rollback failed")
    with (
        patch.object(_product.profiling_config, "remote_config_poc_enabled", True),
        patch_profiler(return_value=profiler),
    ):
        apply_remote_config({"tracing_sampling_rules": [{"sample_rate": 1.0}]}, None)

        assert _product._profiler is profiler
        assert profiler.status == ServiceStatus.RUNNING
        assert _product._cleanup_pending is True
        assert _product._partial_start_cleanup_pending is False
        profiler.stop.assert_called_once_with(flush=True)
        profiler.stop.side_effect = lambda **_: setattr(profiler, "status", ServiceStatus.STOPPED)
        apply_remote_config({}, None)

    assert profiler.stop.call_count == 2
    assert _product._profiler is None
    assert _product._cleanup_pending is False
    assert _product._partial_start_cleanup_pending is False


def test_stopped_start_failure_uses_partial_rollback() -> None:
    profiler = make_profiler()
    profiler.start.side_effect = RuntimeError("start failed")
    with (
        patch.object(_product.profiling_config, "remote_config_poc_enabled", True),
        patch_profiler(return_value=profiler),
    ):
        apply_remote_config({"tracing_sampling_rules": [{"sample_rate": 1.0}]}, None)

    profiler._rollback_start_with_active_lock.assert_called_once_with()
    profiler.stop.assert_not_called()
    assert _product._profiler is None
    assert _product._cleanup_pending is False
    assert _product._partial_start_cleanup_pending is False


def test_stopped_start_failure_retains_profiler_when_partial_rollback_fails() -> None:
    profiler = make_profiler()
    profiler.start.side_effect = RuntimeError("start failed")
    profiler._rollback_start_with_active_lock.side_effect = RuntimeError("rollback failed")
    with (
        patch.object(_product.profiling_config, "remote_config_poc_enabled", True),
        patch_profiler(return_value=profiler),
    ):
        apply_remote_config({"tracing_sampling_rules": [{"sample_rate": 1.0}]}, None)

        assert _product._profiler is profiler
        assert _product._cleanup_pending is True
        assert _product._partial_start_cleanup_pending is True
        profiler._rollback_start_with_active_lock.assert_called_once_with()
        profiler._rollback_start_with_active_lock.side_effect = None
        apply_remote_config({}, None)

    assert profiler._rollback_start_with_active_lock.call_count == 2
    assert _product._profiler is None
    assert _product._cleanup_pending is False
    assert _product._partial_start_cleanup_pending is False


def test_failed_partial_start_blocks_application_profiler_until_cleanup_retry() -> None:
    from ddtrace.profiling import Profiler
    from ddtrace.profiling import profiler as profiler_module

    candidate_internal = Mock(status=ServiceStatus.STOPPED)
    candidate_internal.start.side_effect = RuntimeError("start failed")
    candidate_internal._rollback_start.side_effect = [
        RuntimeError("cleanup failed"),
        RuntimeError("cleanup failed"),
        None,
    ]
    application = object.__new__(Profiler)
    application_internal = Mock(status=ServiceStatus.STOPPED)
    application_internal.start.side_effect = lambda: setattr(application_internal, "status", ServiceStatus.RUNNING)
    application_internal.stop.side_effect = lambda _flush: setattr(
        application_internal, "status", ServiceStatus.STOPPED
    )
    application._profiler = application_internal

    with (
        patch.object(_product.profiling_config, "remote_config_poc_enabled", True),
        patch.object(Profiler, "_active_instance", None),
        patch.object(profiler_module, "_ProfilerInstance", return_value=candidate_internal),
        patch("ddtrace.profiling.profiler.atexit.register"),
        patch("ddtrace.profiling.profiler.atexit.unregister"),
        patch("ddtrace.profiling.profiler.atexit.register_on_exit_signal"),
        patch("ddtrace.profiling.profiler.telemetry_writer.product_activated"),
    ):
        apply_remote_config({"tracing_sampling_rules": [{"sample_rate": 1.0}]}, None)

        failed = _product._profiler
        assert failed is not None
        assert Profiler._active_instance is failed
        assert _product._partial_start_cleanup_pending is True

        application.start()
        application_internal.start.assert_not_called()
        assert Profiler._active_instance is failed

        apply_remote_config({}, None)
        assert _product._profiler is None
        assert Profiler._active_instance is None

        application.start()
        assert application_internal.start.call_count == 1
        assert Profiler._active_instance is application
        application.stop(flush=False)

    assert candidate_internal._rollback_start.call_count == 3
    application_internal.stop.assert_called_once_with(False)


def test_enable_retries_failed_partial_cleanup_before_starting_fresh_profiler() -> None:
    failed = make_profiler()
    replacement = make_profiler()
    failed.start.side_effect = RuntimeError("start failed")
    failed._rollback_start_with_active_lock.side_effect = [RuntimeError("rollback failed"), None]

    with (
        patch.object(_product.profiling_config, "remote_config_poc_enabled", True),
        patch_profiler(side_effect=[failed, replacement]) as profiler_type,
    ):
        apply_remote_config({"tracing_sampling_rules": [{"sample_rate": 1.0}]}, None)
        apply_remote_config({"tracing_sampling_rules": [{"sample_rate": 0.5}]}, None)

    assert failed._rollback_start_with_active_lock.call_count == 2
    assert profiler_type.call_count == 2
    assert _product._profiler is replacement
    assert _product._cleanup_pending is False
    assert _product._partial_start_cleanup_pending is False


def test_enable_retries_failed_stop_before_starting_fresh_profiler() -> None:
    failed = make_profiler()
    replacement = make_profiler()
    failed.start()
    stop_attempts = 0

    def fail_once(**_kwargs: object) -> None:
        nonlocal stop_attempts
        stop_attempts += 1
        if stop_attempts == 1:
            raise RuntimeError("stop failed")
        failed.status = ServiceStatus.STOPPED

    failed.stop.side_effect = fail_once
    _product._profiler = failed

    with (
        patch.object(_product.profiling_config, "remote_config_poc_enabled", True),
        patch_profiler(return_value=replacement) as profiler_type,
    ):
        apply_remote_config({}, None)
        apply_remote_config({"tracing_sampling_rules": [{"sample_rate": 1.0}]}, None)

    assert failed.stop.call_count == 2
    failed._rollback_start_with_active_lock.assert_not_called()
    profiler_type.assert_called_once_with()
    assert _product._profiler is replacement
    assert _product._cleanup_pending is False
    assert _product._partial_start_cleanup_pending is False


def test_stopped_start_return_uses_partial_rollback() -> None:
    profiler = make_profiler()
    profiler.start.side_effect = None
    with (
        patch.object(_product.profiling_config, "remote_config_poc_enabled", True),
        patch_profiler(return_value=profiler),
    ):
        apply_remote_config({"tracing_sampling_rules": [{"sample_rate": 1.0}]}, None)

    profiler._rollback_start_with_active_lock.assert_called_once_with()
    profiler.stop.assert_not_called()
    assert _product._profiler is None
    assert _product._cleanup_pending is False
    assert _product._partial_start_cleanup_pending is False


def test_stopped_start_return_retains_profiler_when_partial_rollback_fails() -> None:
    profiler = make_profiler()
    profiler.start.side_effect = None
    profiler._rollback_start_with_active_lock.side_effect = RuntimeError("rollback failed")
    with (
        patch.object(_product.profiling_config, "remote_config_poc_enabled", True),
        patch_profiler(return_value=profiler),
    ):
        apply_remote_config({"tracing_sampling_rules": [{"sample_rate": 1.0}]}, None)

        assert _product._profiler is profiler
        assert _product._cleanup_pending is True
        assert _product._partial_start_cleanup_pending is True
        profiler._rollback_start_with_active_lock.side_effect = None
        apply_remote_config({}, None)

    assert profiler._rollback_start_with_active_lock.call_count == 2
    assert _product._profiler is None
    assert _product._cleanup_pending is False
    assert _product._partial_start_cleanup_pending is False


def test_lifecycle_worker_retries_requested_disable_without_new_config() -> None:
    profiler = make_profiler()
    profiler.start()
    profiler.stop.side_effect = RuntimeError("stop failed")
    _product._profiler = profiler

    with patch.object(_product.profiling_config, "remote_config_poc_enabled", True):
        apply_remote_config({}, None)

        assert _product._profiler is profiler
        assert _product._cleanup_pending is True
        assert _product._partial_start_cleanup_pending is False

        profiler.stop.side_effect = lambda **_: setattr(profiler, "status", ServiceStatus.STOPPED)
        _product._reconcile_requested()

    assert _product._profiler is None
    assert _product._cleanup_pending is False
    assert _product._partial_start_cleanup_pending is False


def test_stop_error_after_cleanup_releases_owned_profiler() -> None:
    profiler = make_profiler()
    profiler.start()

    def fail_after_cleanup(**_kwargs: object) -> None:
        profiler.status = ServiceStatus.STOPPED
        raise RuntimeError("stop failed")

    profiler.stop.side_effect = fail_after_cleanup
    _product._profiler = profiler

    with patch.object(_product.profiling_config, "remote_config_poc_enabled", True):
        apply_remote_config({}, None)

    assert _product._profiler is None
    assert _product._cleanup_pending is False
    assert _product._partial_start_cleanup_pending is False


def test_interrupted_stop_restores_state_before_reraising() -> None:
    failed = make_profiler()
    failed.start()

    def interrupt_after_cleanup(**_kwargs: object) -> None:
        failed.status = ServiceStatus.STOPPED
        raise KeyboardInterrupt

    failed.stop.side_effect = interrupt_after_cleanup
    replacement = make_profiler()
    _product._profiler = failed

    with (
        patch.object(_product.profiling_config, "remote_config_poc_enabled", True),
        patch_profiler(return_value=replacement),
    ):
        with pytest.raises(KeyboardInterrupt):
            apply_remote_config({}, None)

        assert _product._profiler is None
        assert _product._cleanup_pending is False
        assert _product._partial_start_cleanup_pending is False
        apply_remote_config({"tracing_sampling_rules": [{"sample_rate": 1.0}]}, None)

    assert _product._profiler is replacement


def test_cleanup_retry_error_after_stop_allows_same_request_to_restart() -> None:
    failed = make_profiler()
    failed.start()

    def fail_after_cleanup(**_kwargs: object) -> None:
        failed.status = ServiceStatus.STOPPED
        raise RuntimeError("stop failed")

    failed.stop.side_effect = fail_after_cleanup
    replacement = make_profiler()
    _product._profiler = failed
    _product._cleanup_pending = True

    with (
        patch.object(_product.profiling_config, "remote_config_poc_enabled", True),
        patch_profiler(return_value=replacement) as profiler_type,
    ):
        apply_remote_config({"tracing_sampling_rules": [{"sample_rate": 1.0}]}, None)

    profiler_type.assert_called_once_with()
    assert _product._profiler is replacement
    assert _product._cleanup_pending is False
    assert _product._partial_start_cleanup_pending is False


def test_partial_start_cleanup_retry_error_retains_owned_profiler() -> None:
    profiler = make_profiler()
    profiler._rollback_start_with_active_lock.side_effect = RuntimeError("rollback failed")
    _product._profiler = profiler
    _product._cleanup_pending = True
    _product._partial_start_cleanup_pending = True

    with (
        patch.object(_product.profiling_config, "remote_config_poc_enabled", True),
        patch_profiler() as profiler_type,
    ):
        apply_remote_config({"tracing_sampling_rules": [{"sample_rate": 1.0}]}, None)

    profiler_type.assert_not_called()
    assert _product._profiler is profiler
    assert _product._cleanup_pending is True
    assert _product._partial_start_cleanup_pending is True


def test_existing_profiler_is_not_adopted() -> None:
    from ddtrace.profiling import Profiler

    active = Mock(status=ServiceStatus.RUNNING)
    with (
        patch.object(_product.profiling_config, "remote_config_poc_enabled", True),
        patch.object(Profiler, "_active_instance", active),
        patch.object(Profiler, "__init__", side_effect=AssertionError("must not construct")),
        patch.object(_product.log, "warning") as log_warning,
    ):
        apply_remote_config({"tracing_sampling_rules": [{"sample_rate": 1.0}]}, None)

    assert _product._profiler is None
    log_warning.assert_called_once()


def test_poc_start_is_atomic_with_application_profiler_start() -> None:
    import threading

    from ddtrace.profiling import Profiler
    from ddtrace.profiling import profiler as profiler_module

    construction_started = threading.Event()
    release_construction = threading.Event()
    candidate_internal = Mock(status=ServiceStatus.STOPPED)
    candidate_internal.start.side_effect = lambda: setattr(candidate_internal, "status", ServiceStatus.RUNNING)

    def construct_candidate(*_args: object, **_kwargs: object) -> Mock:
        construction_started.set()
        assert release_construction.wait(timeout=5)
        return candidate_internal

    application = object.__new__(Profiler)
    application_internal = Mock(status=ServiceStatus.STOPPED)
    application_internal.start.side_effect = lambda: setattr(application_internal, "status", ServiceStatus.RUNNING)
    application._profiler = application_internal

    with (
        patch.object(_product.profiling_config, "remote_config_poc_enabled", True),
        patch.object(profiler_module, "_ProfilerInstance", side_effect=construct_candidate),
        patch("ddtrace.profiling.profiler.atexit.register"),
        patch("ddtrace.profiling.profiler.atexit.register_on_exit_signal"),
    ):
        _product.apm_tracing_rc({"tracing_sampling_rules": [{"sample_rate": 1.0}]}, None)
        poc_thread = threading.Thread(target=_product._reconcile_requested)
        poc_thread.start()
        assert construction_started.wait(timeout=5)

        application_thread = threading.Thread(target=application.start)
        application_thread.start()
        time.sleep(0.1)
        release_construction.set()
        poc_thread.join(timeout=5)
        application_thread.join(timeout=5)

    assert not poc_thread.is_alive()
    assert not application_thread.is_alive()
    assert _product._profiler is not None
    assert Profiler._active_instance is _product._profiler
    assert candidate_internal.start.call_count == 1
    assert application_internal.start.call_count == 0

    _product._profiler.stop(flush=False)
    _product._profiler = None


def test_product_start_registers_one_exit_signal_hook() -> None:
    from ddtrace.internal import atexit

    _product._product_state = _product._PRODUCT_INITIALIZING
    with patch.object(atexit, "register_on_exit_signal") as register_on_exit_signal:
        assert _product.start() is None
        assert _product.start() is None

    register_on_exit_signal.assert_called_once_with(_product._stop_on_signal)


def test_before_fork_waits_for_transition() -> None:
    import threading

    entered = threading.Event()
    release = threading.Event()
    before_fork_returned = threading.Event()

    def transition() -> None:
        with _product._lock:
            entered.set()
            assert release.wait(timeout=5)

    transition_thread = threading.Thread(target=transition)
    transition_thread.start()
    assert entered.wait(timeout=5)

    def call_before_fork() -> None:
        _product.before_fork()
        before_fork_returned.set()
        _product._after_fork_parent()

    fork_thread = threading.Thread(target=call_before_fork)
    fork_thread.start()
    time.sleep(0.1)

    assert transition_thread.is_alive()
    assert not before_fork_returned.is_set()

    release.set()
    transition_thread.join(timeout=5)
    fork_thread.join(timeout=5)
    assert not transition_thread.is_alive()
    assert not fork_thread.is_alive()
    assert before_fork_returned.is_set()

    assert _product._fork_lock_holders == {}
    assert _product._lock.acquire(blocking=False)
    _product._lock.release()


@pytest.mark.parametrize("product_state", [_product._PRODUCT_INITIALIZING, _product._PRODUCT_STOPPED])
def test_inactive_product_does_not_touch_fork_lock(product_state: int) -> None:
    lock = Mock()
    _product._product_state = product_state
    _product._lock = lock

    _product.before_fork()
    _product._after_fork_parent()

    lock.assert_not_called()
    lock.acquire.assert_not_called()
    lock.release.assert_not_called()
    assert _product._fork_lock_holders == {}


def test_interrupted_before_fork_releases_acquired_lock() -> None:
    import threading

    underlying = _unpatched.threading_RLock()

    class InterruptingLock:
        def acquire(self, *args: object, **kwargs: object) -> bool:
            acquired = underlying.acquire(*args, **kwargs)
            assert acquired
            raise KeyboardInterrupt

        def release(self) -> None:
            underlying.release()

        def _recursion_count(self) -> int:
            return getattr(underlying, "_recursion_count")()

    _product._lock = InterruptingLock()

    with pytest.raises(KeyboardInterrupt):
        _product.before_fork()

    assert _product._fork_lock_holders == {_product._thread.get_ident(): [False]}
    _product._after_fork_parent()
    acquired = []

    def acquire_after_interrupt() -> None:
        acquired.append(underlying.acquire(timeout=5))
        if acquired[-1]:
            underlying.release()

    worker = threading.Thread(target=acquire_after_interrupt)
    worker.start()
    worker.join(timeout=5)

    assert not worker.is_alive()
    assert acquired == [True]
    assert _product._fork_lock_holders == {}


def test_nested_fork_callbacks_release_each_lock_recursion() -> None:
    import threading

    _product.before_fork()
    _product.before_fork()

    thread_id = _product._thread.get_ident()
    assert _product._fork_lock_holders == {thread_id: [True, True]}

    _product._after_fork_parent()
    assert _product._fork_lock_holders == {thread_id: [True]}
    _product._after_fork_parent()
    assert _product._fork_lock_holders == {}

    acquired = []

    def acquire_after_callbacks() -> None:
        acquired.append(_product._lock.acquire(timeout=5))
        if acquired[-1]:
            _product._lock.release()

    follower = threading.Thread(target=acquire_after_callbacks)
    follower.start()
    follower.join(timeout=5)

    assert not follower.is_alive()
    assert acquired == [True]


def test_interrupted_nested_before_fork_preserves_outer_lock() -> None:
    import threading

    underlying = _unpatched.threading_RLock()
    acquire_count = 0

    class InterruptSecondAcquireLock:
        def acquire(self, *args: object, **kwargs: object) -> bool:
            nonlocal acquire_count
            acquire_count += 1
            if acquire_count == 2:
                raise KeyboardInterrupt
            return underlying.acquire(*args, **kwargs)

        def release(self) -> None:
            underlying.release()

        def _recursion_count(self) -> int:
            return getattr(underlying, "_recursion_count")()

    _product._lock = InterruptSecondAcquireLock()
    _product.before_fork()

    with pytest.raises(KeyboardInterrupt):
        _product.before_fork()

    thread_id = _product._thread.get_ident()
    assert _product._fork_lock_holders == {thread_id: [True, False]}

    _product._after_fork_parent()
    assert _product._fork_lock_holders == {thread_id: [True]}

    acquired_while_outer_held = []

    def acquire_while_outer_held() -> None:
        acquired_while_outer_held.append(underlying.acquire(blocking=False))

    contender = threading.Thread(target=acquire_while_outer_held)
    contender.start()
    contender.join(timeout=5)

    assert not contender.is_alive()
    assert acquired_while_outer_held == [False]

    _product._after_fork_parent()
    assert _product._fork_lock_holders == {}

    acquired = []

    def acquire_after_interrupt() -> None:
        acquired.append(underlying.acquire(timeout=5))
        if acquired[-1]:
            underlying.release()

    follower = threading.Thread(target=acquire_after_interrupt)
    follower.start()
    follower.join(timeout=5)

    assert not follower.is_alive()
    assert acquired == [True]


def test_concurrent_fork_callbacks_release_each_threads_lock() -> None:
    import threading

    underlying = _unpatched.threading_RLock()
    underlying.acquire()
    waiters = 0
    waiters_lock = threading.Lock()
    both_waiting = threading.Event()

    class TrackingLock:
        def acquire(self, *args: object, **kwargs: object) -> bool:
            nonlocal waiters
            with waiters_lock:
                waiters += 1
                if waiters == 2:
                    both_waiting.set()
            return underlying.acquire(*args, **kwargs)

        def release(self) -> None:
            underlying.release()

        def _recursion_count(self) -> int:
            return getattr(underlying, "_recursion_count")()

    _product._lock = TrackingLock()
    completed = []

    def fork_callbacks() -> None:
        _product.before_fork()
        _product._after_fork_parent()
        completed.append(_product._thread.get_ident())

    workers = [threading.Thread(target=fork_callbacks) for _ in range(2)]
    for worker in workers:
        worker.start()

    assert both_waiting.wait(timeout=5)
    underlying.release()
    for worker in workers:
        worker.join(timeout=5)

    assert all(not worker.is_alive() for worker in workers)
    assert len(completed) == 2
    assert _product._fork_lock_holders == {}

    acquired = []

    def acquire_after_forks() -> None:
        acquired.append(underlying.acquire(timeout=5))
        if acquired[-1]:
            underlying.release()

    follower = threading.Thread(target=acquire_after_forks)
    follower.start()
    follower.join(timeout=5)

    assert not follower.is_alive()
    assert acquired == [True]


@pytest.mark.skipif(not hasattr(__import__("os"), "fork"), reason="fork is unavailable")
@pytest.mark.subprocess(
    parametrize={"transition": ["start", "stop"]},
    env={"DD_REMOTE_CONFIGURATION_ENABLED": "true"},
    err=None,
)
def test_fork_waits_for_stable_profiler_transition() -> None:
    import os
    import threading
    import time
    from unittest import mock

    from ddtrace.internal import forksafe
    from ddtrace.internal import profiling_product as _product
    from ddtrace.internal.service import ServiceStatus

    transition = os.environ["transition"]
    entered = threading.Event()
    release = threading.Event()

    class BlockingProfiler:
        _active_instance = None
        _active_lock = threading.RLock()

        def __init__(self) -> None:
            self.status = ServiceStatus.STOPPED
            self._service_lock = threading.Lock()

        def _start_with_active_lock(self, **_kwargs: object) -> None:
            with self._service_lock:
                if transition == "start":
                    entered.set()
                    assert release.wait(timeout=5)
                self.status = ServiceStatus.RUNNING
                BlockingProfiler._active_instance = self

        def stop(self, flush: bool = True) -> None:
            with self._service_lock:
                if transition == "stop":
                    entered.set()
                    assert release.wait(timeout=5)
                self.status = ServiceStatus.STOPPED
                BlockingProfiler._active_instance = None

    forksafe._registry_before_fork[:] = [_product.before_fork]
    forksafe._registry[:] = [forksafe._reset_objects, _product.restart]
    forksafe._registry_after_parent[:] = [_product._after_fork_parent]
    _product._product_state = _product._PRODUCT_RUNNING

    with (
        mock.patch.object(_product.profiling_config, "remote_config_poc_enabled", True),
        mock.patch("ddtrace.profiling.profiler.Profiler", BlockingProfiler),
    ):
        if transition == "stop":
            _product.apm_tracing_rc({"tracing_sampling_rules": [{"sample_rate": 1.0}]}, None)
            _product._reconcile_requested()

        requested = transition == "start"
        worker = threading.Thread(target=_product._set_requested, args=(requested,))
        worker.start()
        assert entered.wait(timeout=5)

        def release_transition() -> None:
            time.sleep(0.1)
            release.set()

        releaser = threading.Thread(target=release_transition)
        releaser.start()
        child = os.fork()
        if child == 0:
            try:
                if transition == "start":
                    assert _product._profiler is not None
                    assert _product._profiler.status == ServiceStatus.RUNNING
                    assert BlockingProfiler._active_instance is _product._profiler
                else:
                    assert _product._profiler is None
                    assert BlockingProfiler._active_instance is None
                assert _product._fork_lock_holders == {}
                _product.apm_tracing_rc({}, None)
                assert _product._desired_requested is False
            except BaseException:
                os._exit(1)
            os._exit(0)

        worker.join(timeout=5)
        releaser.join(timeout=5)
        assert not worker.is_alive()
        assert not releaser.is_alive()
        _, status = os.waitpid(child, 0)
        assert os.waitstatus_to_exitcode(status) == 0


def test_restart_resets_controller_lock_in_child() -> None:
    original = _product._lock
    _product.before_fork()

    forksafe._reset_objects()
    assert _product.restart() is None
    assert _product._lock is original
    assert _product._fork_lock_holders == {}
    assert _product._fork_in_progress is False
    assert _product._lock.acquire(blocking=False)
    _product._lock.release()


def test_noop_post_preload_hook() -> None:
    assert _product.post_preload() is None


@pytest.mark.subprocess(
    env={
        "DD_PROFILING_ENABLED": "false",
        "DD_REMOTE_CONFIGURATION_ENABLED": "true",
        "_DD_PROFILING_REMOTE_CONFIG_POC_ENABLED": "true",
    },
    err=None,
)
def test_real_profiler_can_run_two_remote_config_cycles() -> None:
    import gc
    from unittest import mock
    import weakref

    from ddtrace.internal import profiling_product as _product
    from ddtrace.internal._threads import periodic_threads
    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.internal.service import ServiceStatus
    from ddtrace.profiling import profiler as profiler_module

    _product.start()

    def update(config: dict[str, object]) -> None:
        _product.apm_tracing_rc(config, None)
        _product._reconcile_requested()

    def scheduler_threads() -> list[str]:
        return [
            thread.name
            for thread in periodic_threads.values()
            if thread.name == "ddtrace.profiling.scheduler:Scheduler"
        ]

    with (
        mock.patch.object(ddup, "upload") as upload,
        mock.patch.object(profiler_module.atexit, "register_on_exit_signal") as register_profiler_signal,
    ):
        update({"tracing_sampling_rules": [{"sample_rate": 1.0}]})
        first = _product._profiler
        assert first is not None
        assert first.status == ServiceStatus.RUNNING
        assert len(scheduler_threads()) == 1

        update({"exception_replay_enabled": True})
        assert _product._profiler is None
        assert scheduler_threads() == []
        assert upload.call_count == 1
        first_ref = weakref.ref(first)
        del first
        gc.collect()
        assert first_ref() is None

        update({"tracing_sampling_rules": [{"sample_rate": 1.0}]})
        second = _product._profiler
        assert second is not None
        assert second.status == ServiceStatus.RUNNING
        assert len(scheduler_threads()) == 1

        update({"tracing_sampling_rules": []})
        assert _product._profiler is None
        assert scheduler_threads() == []
        assert upload.call_count == 2
        second_ref = weakref.ref(second)
        del second
        gc.collect()
        assert second_ref() is None
        register_profiler_signal.assert_not_called()


@pytest.mark.skipif(sys.platform == "win32", reason="SIGTERM not supported on Windows")
@pytest.mark.subprocess(
    env={
        "DD_PROFILING_ENABLED": "false",
        "DD_REMOTE_CONFIGURATION_ENABLED": "true",
        "_DD_PROFILING_REMOTE_CONFIG_POC_ENABLED": "true",
    },
    status=-15,
    out=lambda output: output.count("flushed") == 1,
    err=None,
)
def test_worker_started_product_profiler_flushes_once_on_sigterm() -> None:
    import os
    import signal
    import threading
    import time
    from unittest import mock

    from ddtrace.internal import profiling_product as _product
    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.internal.service import ServiceStatus

    _product.start()
    with mock.patch.object(ddup, "upload", lambda *args, **kwargs: print("flushed", flush=True)):
        worker = threading.Thread(
            target=_product.apm_tracing_rc,
            args=({"tracing_sampling_rules": [{"sample_rate": 1.0}]}, None),
        )
        worker.start()
        worker.join()

        deadline = time.monotonic() + 5
        while _product._profiler is None or _product._profiler.status != ServiceStatus.RUNNING:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert _product._profiler is not None
        assert _product._profiler.status == ServiceStatus.RUNNING
        os.kill(os.getpid(), signal.SIGTERM)


@pytest.mark.skipif(sys.platform == "win32", reason="SIGTERM not supported on Windows")
@pytest.mark.subprocess(
    env={
        "DD_PROFILING_ENABLED": "false",
        "DD_REMOTE_CONFIGURATION_ENABLED": "true",
        "_DD_PROFILING_REMOTE_CONFIG_POC_ENABLED": "true",
    },
    status=-15,
    out=lambda output: output.count("flushed") == 0,
    err=None,
)
def test_sigterm_does_not_wait_for_worker_start() -> None:
    import os
    import signal
    import threading
    from unittest import mock

    from ddtrace.internal import profiling_product as _product
    from ddtrace.internal.service import ServiceStatus

    start_entered = threading.Event()
    release_start = threading.Event()

    class BlockingProfiler:
        _active_instance = None
        _active_lock = threading.RLock()

        def __init__(self) -> None:
            self.status = ServiceStatus.STOPPED

        def start(self, **_kwargs: object) -> None:
            start_entered.set()
            release_start.wait()
            self.status = ServiceStatus.RUNNING

        _start_with_active_lock = start

        def _stop_on_signal(self) -> None:
            print("flushed", flush=True)
            self.status = ServiceStatus.STOPPED

    _product.start()
    with mock.patch("ddtrace.profiling.profiler.Profiler", BlockingProfiler):
        worker = threading.Thread(
            target=_product.apm_tracing_rc,
            args=({"tracing_sampling_rules": [{"sample_rate": 1.0}]}, None),
        )
        worker.start()
        assert start_entered.wait(timeout=5)

        os.kill(os.getpid(), signal.SIGTERM)


@pytest.mark.skipif(sys.platform == "win32", reason="SIGTERM not supported on Windows")
@pytest.mark.subprocess(
    env={
        "DD_PROFILING_ENABLED": "false",
        "DD_REMOTE_CONFIGURATION_ENABLED": "true",
        "_DD_PROFILING_REMOTE_CONFIG_POC_ENABLED": "true",
    },
    status=-15,
    out=lambda output: output.count("flushed") == 0,
    err=None,
)
def test_sigterm_does_not_wait_for_worker_stop() -> None:
    import os
    import signal
    import threading
    from unittest import mock

    from ddtrace.internal import profiling_product as _product
    from ddtrace.internal.service import ServiceStatus

    stop_entered = threading.Event()
    release_stop = threading.Event()

    class BlockingProfiler:
        _active_instance = None
        _active_lock = threading.RLock()

        def __init__(self) -> None:
            self.status = ServiceStatus.STOPPED

        def start(self, **_kwargs: object) -> None:
            self.status = ServiceStatus.RUNNING

        _start_with_active_lock = start

        def stop(self, flush: bool = True) -> None:
            stop_entered.set()
            release_stop.wait()
            if flush:
                print("flushed", flush=True)
            self.status = ServiceStatus.STOPPED

    _product.start()
    with mock.patch("ddtrace.profiling.profiler.Profiler", BlockingProfiler):
        _product.apm_tracing_rc({"tracing_sampling_rules": [{"sample_rate": 1.0}]}, None)
        _product._reconcile_requested()
        worker = threading.Thread(target=_product.apm_tracing_rc, args=({}, None))
        worker.start()
        assert stop_entered.wait(timeout=5)

        os.kill(os.getpid(), signal.SIGTERM)


@pytest.mark.skipif(sys.platform == "win32", reason="SIGTERM not supported on Windows")
@pytest.mark.subprocess(
    env={
        "DD_PROFILING_ENABLED": "false",
        "DD_REMOTE_CONFIGURATION_ENABLED": "true",
        "_DD_PROFILING_REMOTE_CONFIG_POC_ENABLED": "true",
    },
    status=-15,
    err=None,
)
def test_sigterm_does_not_reacquire_active_lock_for_partial_cleanup() -> None:
    import os
    import signal
    from unittest import mock

    from ddtrace.internal import profiling_product as _product
    from ddtrace.internal.service import ServiceStatus
    from ddtrace.profiling import Profiler

    owned = mock.Mock(status=ServiceStatus.STOPPED)
    _product.start()
    _product._profiler = owned
    _product._cleanup_pending = True
    _product._partial_start_cleanup_pending = True
    Profiler._active_instance = owned
    Profiler._active_lock.acquire()

    os.kill(os.getpid(), signal.SIGTERM)


@pytest.mark.subprocess(
    env={
        "DD_PROFILING_ENABLED": "true",
        "DD_REMOTE_CONFIGURATION_ENABLED": "true",
        "_DD_PROFILING_REMOTE_CONFIG_POC_ENABLED": "true",
    },
    ddtrace_run=True,
    err=None,
)
def test_automatic_profiler_wins_when_remote_config_poc_is_also_enabled() -> None:
    from ddtrace.internal import profiling_product as _product
    from ddtrace.profiling import bootstrap
    from ddtrace.profiling import profiler

    automatic_profiler = bootstrap.profiler
    assert profiler.Profiler._active_instance is automatic_profiler
    assert _product.enabled() is False
    assert _product._profiler is None

    _product.apm_tracing_rc({"tracing_sampling_rules": [{"sample_rate": 1.0}]}, None)

    assert profiler.Profiler._active_instance is automatic_profiler
    assert _product._profiler is None
    automatic_profiler.stop(flush=False)
