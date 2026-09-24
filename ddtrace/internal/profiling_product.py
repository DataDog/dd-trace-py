import _thread
import os
import typing as t

from ddtrace.internal import _unpatched
from ddtrace.internal import atexit
from ddtrace.internal import forksafe
from ddtrace.internal import periodic
from ddtrace.internal import threads as internal_threads
from ddtrace.internal.logger import get_logger
from ddtrace.internal.service import ServiceStatus
from ddtrace.internal.service import ServiceStatusError
from ddtrace.internal.settings._config import config as global_config
from ddtrace.internal.settings.profiling import config as profiling_config


requires = ["apm-tracing-rc"]
log = get_logger(__name__)
_lock = forksafe.ResetObject(_unpatched.threading_RLock)
_request_lock = forksafe.Lock()
_PRODUCT_INITIALIZING = 0
_PRODUCT_RUNNING = 1
_PRODUCT_STOPPED = 2
_product_state = _PRODUCT_INITIALIZING
_desired_requested: t.Optional[bool] = None
_admission_open = True
_fork_in_progress = False
# AIDEV-NOTE: Per-thread frames pair nested before/parent callbacks without
# releasing a lock that an interrupted before hook did not acquire.
_fork_lock_holders: dict[int, list[bool]] = {}
_exit_signal_registered = False
_lifecycle_worker: t.Optional["_LifecycleWorker"] = None
_profiler: t.Optional[t.Any] = None
_cleanup_pending = False
_partial_start_cleanup_pending = False
_RETRY_INTERVAL = 1.0

# AIDEV-NOTE: This POC deliberately treats non-empty tracing_sampling_rules as
# "profiling on" and ignores unrelated merged APM tracing fields. Do not promote
# this mapping to production behavior; a released implementation needs its own
# RC schema field.


class _LifecycleWorker(periodic.PeriodicService):
    def __init__(self) -> None:
        super().__init__(interval=_RETRY_INTERVAL, no_wait_at_start=True)

    def periodic(self) -> None:
        _reconcile_requested()

    def _rollback_start(self) -> None:
        with self._service_lock:
            if self._worker is not None:
                # AIDEV-NOTE: The fork lock makes cancellation and native-worker classification atomic with restart.
                with internal_threads._forking_lock:
                    if self._worker._cancel_deferred_start_unlocked():
                        self._worker = None
                    else:
                        try:
                            self._stop_service()
                        except RuntimeError as error:
                            if str(error) != "Thread not started":
                                raise
                            try:
                                self._worker.join(0)
                            except RuntimeError as join_error:
                                if str(join_error) != "Periodic thread not started":
                                    raise
                                self._worker = None
                            else:
                                self._stop_service()
            self.status = ServiceStatus.STOPPED


def enabled() -> bool:
    return (
        global_config._remote_config_enabled
        and profiling_config.remote_config_poc_enabled
        and not profiling_config.enabled
    )


def start() -> None:
    global _admission_open, _exit_signal_registered, _lifecycle_worker, _product_state

    worker: t.Optional[_LifecycleWorker] = None
    join_failed_worker = False
    try:
        with _lock:
            if _product_state != _PRODUCT_INITIALIZING:
                return
            if not _exit_signal_registered:
                atexit.register_on_exit_signal(_stop_on_signal)
                _exit_signal_registered = True

            worker = _LifecycleWorker()
            with _request_lock:
                _admission_open = True
                _lifecycle_worker = worker
            _product_state = _PRODUCT_RUNNING

            try:
                worker.start()
            except BaseException:
                with _request_lock:
                    _admission_open = False
                    _lifecycle_worker = None
                _product_state = _PRODUCT_STOPPED
                try:
                    worker._rollback_start()
                except BaseException as error:
                    if isinstance(error, Exception):
                        log.exception("Profiling RC POC failed to roll back lifecycle worker")
                else:
                    join_failed_worker = True
                raise
    except BaseException:
        # The worker can be waiting for _lock as soon as its thread starts, so
        # join it only after the failed startup has released product ownership.
        if join_failed_worker and worker is not None:
            try:
                worker.join()
            except BaseException as error:
                if isinstance(error, Exception):
                    log.exception("Profiling RC POC failed to join lifecycle worker")
        raise


def post_preload() -> None:
    pass


def before_fork() -> None:
    global _fork_in_progress

    if _product_state != _PRODUCT_RUNNING and _lifecycle_worker is None and _profiler is None:
        return

    # AIDEV-NOTE: Waiting for a stable transition keeps the child from inheriting
    # profiler ownership that no surviving thread can reconcile safely.
    with _request_lock:
        _fork_in_progress = True

    thread_id = _thread.get_ident()
    frames = _fork_lock_holders.setdefault(thread_id, [])
    recursion_count = getattr(_lock, "_recursion_count")
    lock_depth = recursion_count()
    frames.append(False)

    try:
        _lock.acquire()
        frames[-1] = True
    except BaseException:
        frames[-1] = False
        while recursion_count() > lock_depth:
            _lock.release()
        raise


@forksafe.register_after_parent
def _after_fork_parent() -> None:
    global _fork_in_progress

    thread_id = _thread.get_ident()
    frames = _fork_lock_holders.get(thread_id)

    if frames:
        acquired = frames.pop()
        if not frames:
            del _fork_lock_holders[thread_id]
        if acquired:
            _lock.release()

    with _request_lock:
        _fork_in_progress = bool(_fork_lock_holders)


def restart(join: bool = False) -> None:
    global _fork_in_progress, _fork_lock_holders

    _fork_lock_holders = {}
    with _request_lock:
        _fork_in_progress = False


def _reconcile_requested() -> None:
    with _request_lock:
        if not _admission_open or _fork_in_progress:
            return
        requested = _desired_requested

    if requested is not None:
        _set_requested(requested)


def _stop_owned_profiler(profiler: t.Any, partial_start_cleanup_pending: bool, active_lock_held: bool = False) -> None:
    if partial_start_cleanup_pending:
        if active_lock_held:
            profiler._rollback_start_with_active_lock()
        else:
            from ddtrace.profiling.profiler import Profiler

            with Profiler._active_lock:
                profiler._rollback_start_with_active_lock()
    else:
        if active_lock_held:
            profiler._stop_with_active_lock(flush=True)
        else:
            profiler.stop(flush=True)


def _clear_profiler_state() -> None:
    global _cleanup_pending, _partial_start_cleanup_pending, _profiler

    _profiler = None
    _cleanup_pending = False
    _partial_start_cleanup_pending = False


def _stop_on_signal() -> None:
    if getattr(_lock, "_is_owned")():
        return

    if not _lock.acquire(blocking=False):
        return
    try:
        if _profiler is None:
            return

        profiler = _profiler
        try:
            if _partial_start_cleanup_pending:
                from ddtrace.profiling.profiler import Profiler

                if not Profiler._active_lock.acquire(blocking=False):
                    return
                try:
                    _stop_owned_profiler(
                        profiler,
                        partial_start_cleanup_pending=True,
                        active_lock_held=True,
                    )
                finally:
                    Profiler._active_lock.release()
            else:
                profiler._stop_on_signal()
        except Exception:
            log.debug("Profiling RC POC failed to stop profiler on exit signal", exc_info=True)
            return

        _clear_profiler_state()
    finally:
        _lock.release()


def _rollback_failed_start(
    profiler: t.Any, partial_start_cleanup_pending: bool, active_lock_held: bool = False
) -> None:
    global _cleanup_pending, _partial_start_cleanup_pending, _profiler

    _cleanup_pending = True
    _partial_start_cleanup_pending = partial_start_cleanup_pending
    try:
        _stop_owned_profiler(profiler, _partial_start_cleanup_pending, active_lock_held)
    except Exception:
        log.exception("Profiling RC POC failed to roll back profiler in process %d", os.getpid())
        return

    if profiler.status != ServiceStatus.RUNNING:
        _clear_profiler_state()


def _set_requested(requested: bool, closing: bool = False) -> None:
    global _cleanup_pending, _partial_start_cleanup_pending, _product_state, _profiler

    _lock.acquire()
    try:
        if closing:
            _product_state = _PRODUCT_STOPPED
        elif _product_state == _PRODUCT_INITIALIZING:
            return
        elif _product_state == _PRODUCT_STOPPED:
            return

        if _profiler is not None and _cleanup_pending:
            try:
                _stop_owned_profiler(_profiler, _partial_start_cleanup_pending)
            except BaseException as error:
                if isinstance(error, Exception):
                    log.exception("Profiling RC POC failed to roll back profiler in process %d", os.getpid())
                if _partial_start_cleanup_pending or _profiler.status == ServiceStatus.RUNNING:
                    if not isinstance(error, Exception):
                        raise
                    return
                if not isinstance(error, Exception):
                    _clear_profiler_state()
                    raise

            _clear_profiler_state()

        if requested:
            if _profiler is not None:
                return

            from ddtrace.profiling.profiler import Profiler

            with Profiler._active_lock:
                active = Profiler._active_instance
                if active is not None:
                    log.warning(
                        "Profiling RC POC in process %d will not adopt an existing profiler",
                        os.getpid(),
                    )
                    return

                try:
                    profiler = Profiler()
                except BaseException as error:
                    if not isinstance(error, Exception):
                        raise
                    log.exception("Profiling RC POC failed to start profiler in process %d", os.getpid())
                    return

                _profiler = profiler
                _cleanup_pending = False
                _partial_start_cleanup_pending = False
                try:
                    profiler._start_with_active_lock(register_on_exit_signal=False)
                except BaseException as error:
                    if isinstance(error, Exception):
                        log.exception("Profiling RC POC failed to start profiler in process %d", os.getpid())
                    _rollback_failed_start(
                        profiler,
                        profiler.status == ServiceStatus.STOPPED,
                        active_lock_held=True,
                    )
                    if not isinstance(error, Exception):
                        raise
                    return

                if profiler.status != ServiceStatus.RUNNING:
                    log.error("Profiling RC POC profiler did not start in process %d", os.getpid())
                    _rollback_failed_start(
                        profiler,
                        partial_start_cleanup_pending=True,
                        active_lock_held=True,
                    )
                    return

                log.info("Profiling RC POC started profiler in process %d", os.getpid())
                return

        if _profiler is None:
            return

        try:
            _stop_owned_profiler(_profiler, partial_start_cleanup_pending=False)
        except BaseException as error:
            if isinstance(error, Exception):
                log.exception("Profiling RC POC failed to stop profiler in process %d", os.getpid())
            if _profiler.status != ServiceStatus.RUNNING:
                _clear_profiler_state()
            else:
                _cleanup_pending = True
                _partial_start_cleanup_pending = False
            if not isinstance(error, Exception):
                raise
            return

        _clear_profiler_state()
        log.info("Profiling RC POC stopped profiler in process %d", os.getpid())
    finally:
        _lock.release()


def stop(join: bool = False) -> None:
    global _admission_open, _desired_requested, _lifecycle_worker

    with _request_lock:
        _admission_open = False
        _desired_requested = False
        worker = _lifecycle_worker
        _lifecycle_worker = None

    if worker is not None:
        try:
            worker.stop()
        except ServiceStatusError:
            pass
        worker.join()

    _set_requested(False, closing=True)


def apm_tracing_rc(lib_config: dict[str, object], _config: object) -> None:
    global _desired_requested

    if not enabled():
        return

    with _request_lock:
        if not _admission_open:
            return
        _desired_requested = bool(lib_config.get("tracing_sampling_rules"))
