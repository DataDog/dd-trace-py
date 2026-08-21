# -*- encoding: utf-8 -*-
import json
import logging
from typing import Any
from typing import Callable
from typing import Mapping
from typing import Optional
from typing import Union
from typing import cast

import ddtrace
from ddtrace import config
from ddtrace.internal import atexit
from ddtrace.internal import forksafe
from ddtrace.internal import process_tags
from ddtrace.internal import service
from ddtrace.internal import uwsgi
from ddtrace.internal.datadog.profiling import ddup
from ddtrace.internal.forksafe import Lock
from ddtrace.internal.module import ModuleWatchdog
from ddtrace.internal.settings import env as _env
from ddtrace.internal.settings.profiling import config as profiling_config
from ddtrace.internal.settings.profiling import config_str
from ddtrace.internal.telemetry import telemetry_writer
from ddtrace.internal.telemetry.constants import TELEMETRY_APM_PRODUCT
from ddtrace.profiling import collector
from ddtrace.profiling import scheduler
from ddtrace.profiling.collector import asyncio
from ddtrace.profiling.collector import exception
from ddtrace.profiling.collector import memalloc
from ddtrace.profiling.collector import pytorch
from ddtrace.profiling.collector import stack
from ddtrace.profiling.collector import threading


LOG = logging.getLogger(__name__)


class Profiler(object):
    """Run profiling while code is executed.

    Note that the whole Python process is profiled, not only the code executed. Data from all running threads are
    caught.

    """

    _active_instance: Optional["Profiler"] = None
    _active_lock = Lock()

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._profiler: "_ProfilerInstance" = _ProfilerInstance(*args, **kwargs)

    def start(self) -> None:
        """Start the profiler."""
        with Profiler._active_lock:
            self._start_with_active_lock()

    def _start_with_active_lock(self, register_on_exit_signal: bool = True) -> None:
        if not self._active_instance_available_with_active_lock():
            return

        try:
            uwsgi.check_uwsgi(self._start_on_fork, atexit=self.stop)
        except uwsgi.uWSGIMasterProcess:
            # Do nothing in master, the profiler will be started in each worker via _start_on_fork
            return
        except uwsgi.uWSGIConfigDeprecationWarning:
            LOG.warning("uWSGI configuration deprecation warning", exc_info=True)
            # Turn off profiling in this case, this is mostly for
            # uwsgi<2.0.30 when --skip-atexit is not set with --lazy-apps
            # or --lazy. See uwsgi.check_uwsgi() for details.
            return

        # AIDEV-NOTE: Process-global ownership remains reserved until startup rollback succeeds.
        Profiler._active_instance = self
        self._start_profiler_with_active_lock()

        telemetry_activation_attempted = False
        try:
            atexit.register(self.stop)

            telemetry_activation_attempted = True
            telemetry_writer.product_activated(TELEMETRY_APM_PRODUCT.PROFILER, True)

            # register_on_exit_signal is needed for processes terminated via SIGTERM (e.g.
            # Ray workers, Kubernetes pods). Python atexit handlers do NOT run on SIGTERM by default,
            # so without this the last partial profile window is silently lost.
            # We register _stop_on_signal (not stop) to avoid deadlocking when SIGTERM arrives while
            # _active_lock is already held by the main thread (e.g. during start or stop).
            if register_on_exit_signal:
                atexit.register_on_exit_signal(self._stop_on_signal)

            # Note: For regular fork(), native pthread_atfork handlers restart the sampling thread
            # and PeriodicThread auto-restart handles the Scheduler. No explicit forksafe hook needed.
            # For uWSGI, _start_on_fork is registered via uwsgidecorators.postfork() in check_uwsgi().

        except BaseException:
            # AIDEV-NOTE: Startup bookkeeping failure triggers rollback; process ownership remains
            # reserved until rollback succeeds, including while a cleanup retry is pending.
            if telemetry_activation_attempted:
                try:
                    telemetry_writer.product_activated(TELEMETRY_APM_PRODUCT.PROFILER, False)
                except BaseException:
                    LOG.debug("Failed to deactivate profiler telemetry after startup failed", exc_info=True)
            try:
                self._rollback_start_with_active_lock()
            except BaseException:
                LOG.debug("Failed to clean up profiler after startup bookkeeping failed", exc_info=True)
            raise

    def _start_profiler_with_active_lock(self) -> None:
        try:
            self._profiler.start()
        except BaseException:
            if self._profiler.status == service.ServiceStatus.STOPPED:
                try:
                    self._rollback_start_with_active_lock()
                except BaseException:
                    LOG.debug("Failed to clean up partially started profiler", exc_info=True)
            raise

    def stop(self, flush: bool = True) -> None:
        """Stop the profiler.

        :param flush: Flush last profile.
        """
        try:
            with Profiler._active_lock:
                self._stop_with_active_lock(flush)
        except service.ServiceStatusError:
            # Not a best practice, but for backward API compatibility that allowed to call `stop` multiple times.
            pass

    def _stop_with_active_lock(self, flush: bool = True) -> None:
        atexit.unregister(self.stop)
        if self._profiler._start_cleanup_pending:
            self._profiler._rollback_start(flush=flush)
            self._finalize_stop_with_active_lock()
            return
        try:
            self._profiler.stop(flush)
        except BaseException:
            if self._profiler.status == service.ServiceStatus.RUNNING:
                try:
                    self._profiler._rollback_start(flush=flush)
                except BaseException:
                    LOG.debug("Failed to finish profiler cleanup", exc_info=True)
                else:
                    self._finalize_stop_with_active_lock()
            raise
        self._finalize_stop_with_active_lock()

    def _finalize_stop_with_active_lock(self) -> None:
        if Profiler._active_instance is self:
            Profiler._active_instance = None
        telemetry_writer.product_activated(TELEMETRY_APM_PRODUCT.PROFILER, False)

    def _rollback_start_with_active_lock(self) -> None:
        atexit.unregister(self.stop)
        self._profiler._rollback_start(flush=False)
        if Profiler._active_instance is self:
            Profiler._active_instance = None

    def _active_instance_available_with_active_lock(self) -> bool:
        active = Profiler._active_instance
        if active is None or active is self:
            return True
        # AIDEV-NOTE: A child can inherit ownership while another thread is rolling startup back.
        # Finish that inherited transaction before deciding whether a replacement may start.
        cleanup_generation = getattr(active._profiler, "_start_cleanup_generation", None)
        if (
            active._profiler._start_cleanup_pending
            and isinstance(cleanup_generation, int)
            and cleanup_generation != forksafe.get_generation()
        ):
            try:
                active._rollback_start_with_active_lock()
            except BaseException:
                LOG.debug("Failed to clean up the active profiler before starting another", exc_info=True)
                return False
            return True
        LOG.error(
            "A profiler is already running. Only one profiler instance can be active at a time. "
            "The second profiler will not be started."
        )
        return False

    def _stop_on_signal(self) -> None:
        """Flush and stop the profiler when an exit signal (SIGTERM/SIGINT) is received.

        Signal handlers run in the main thread between bytecodes. If the main thread already
        holds _active_lock (e.g. SIGTERM races with start or stop), a blocking acquire
        would deadlock. We use non-blocking acquire and bail out when the lock is unavailable:
        in that case start/stop is already in progress and will handle cleanup itself.

        This mirrors the pattern used by the tracer's shutdown method.
        """
        atexit.unregister(self.stop)

        if not Profiler._active_lock.acquire(blocking=False):
            # If the lock is unavailable, stop is already running on the main thread
            # (signal handlers are delivered between bytecodes of the main thread, so the main
            # thread itself holds the lock). A blocking acquire here would deadlock. We bail out
            # and rely on the in-progress stop to complete the flush. The narrow race where
            # _raise_default terminates the process before stop finishes is a known
            # limitation: there is no safe way to wait for a lock held by the
            # current thread from within a signal handler.
            return

        try:
            self._stop_with_active_lock(flush=True)
        except service.ServiceStatusError:
            pass
        except Exception:
            LOG.debug("Exception while stopping profiler on exit signal", exc_info=True)
        finally:
            Profiler._active_lock.release()

    def _start_on_fork(self) -> None:
        """Start a fresh profiler in child process after fork. This is needed for uWSGI support."""
        with Profiler._active_lock:
            if not self._active_instance_available_with_active_lock():
                return
            active = Profiler._active_instance
            if (
                active is self
                and self._profiler.status == service.ServiceStatus.RUNNING
                and not self._profiler._start_cleanup_pending
            ):
                return

            Profiler._active_instance = self
            self._start_profiler_with_active_lock()

    def __getattr__(self, key: str) -> Any:
        return getattr(self._profiler, key)


class _ProfilerInstance(service.Service):
    """An instance of the profiler.

    Each process must manage its own instance.

    """

    def __init__(
        self,
        service: Optional[str] = None,
        tags: Optional[dict[str, str]] = None,
        env: Optional[str] = None,
        version: Optional[str] = None,
        tracer: Any = ddtrace.tracer,
        api_key: Optional[str] = None,
        _memory_collector_enabled: bool = profiling_config.memory.enabled,
        _stack_collector_enabled: bool = profiling_config.stack.enabled,
        _lock_collector_enabled: bool = profiling_config.lock.enabled,
        _pytorch_collector_enabled: bool = profiling_config.pytorch.enabled,
        _exception_profiling_enabled: bool = profiling_config.exception.enabled,
        enable_code_provenance: bool = profiling_config.code_provenance,
        endpoint_collection_enabled: bool = profiling_config.endpoint_collection,
    ):
        super().__init__()
        # User-supplied values
        self.service: Optional[str] = service if service is not None else config.service
        self.tags: dict[str, str] = dict(tags if tags is not None else profiling_config.tags)
        self.env: Optional[str] = env if env is not None else config.env
        self.version: Optional[str] = version if version is not None else config.version
        self.tracer: Any = tracer
        self.api_key: Optional[str] = api_key if api_key is not None else config._dd_api_key
        self._memory_collector_enabled: bool = _memory_collector_enabled
        self._stack_collector_enabled: bool = _stack_collector_enabled
        self._lock_collector_enabled: bool = _lock_collector_enabled
        self._pytorch_collector_enabled: bool = _pytorch_collector_enabled
        self._exception_profiling_enabled: bool = _exception_profiling_enabled
        self.enable_code_provenance: bool = enable_code_provenance
        self.endpoint_collection_enabled: bool = endpoint_collection_enabled

        # Non-user-supplied values
        # Note: memalloc.MemoryCollector is not a subclass of collector.Collector, so we need to use a union type.
        #       This is because its snapshot method cannot be static.
        self._collectors: list[collector.Collector | memalloc.MemoryCollector] = []
        self._collectors_pending_cleanup: list[collector.Collector | memalloc.MemoryCollector] = []
        # AIDEV-NOTE: Retry cleanup completes before any profiler component is restarted.
        self._start_cleanup_pending = False
        self._start_cleanup_generation = forksafe.get_generation()
        # AIDEV-NOTE: Retried teardown must attempt to flush each profiling session at most once.
        self._stop_flush_attempted = False
        self._collectors_on_import: list[tuple[str, Callable[[Any], None]]] = []
        self._collectors_on_import_registered: list[tuple[str, Callable[[Any], None]]] = []
        self._scheduler: Optional[Union[scheduler.Scheduler, scheduler.ServerlessScheduler]] = None
        self._lambda_function_name: Optional[str] = _env.get("AWS_LAMBDA_FUNCTION_NAME")

        self.process_tags: Optional[str] = process_tags.process_tags or None

        try:
            self.__post_init__()
        except BaseException:
            for error in self._unregister_import_hooks():
                LOG.debug(
                    "Failed to clean up partially constructed profiler",
                    exc_info=(type(error), error, error.__traceback__),
                )
            raise

    def __eq__(self, other: Any) -> bool:
        for k, v in vars(self).items():
            if k.startswith("_") or k in self._COPY_IGNORE_ATTRIBUTES:
                continue
            if v != getattr(other, k, None):
                return False
        return True

    def _build_default_exporters(self) -> None:
        if self._lambda_function_name is not None:
            self.tags.update({"functionname": self._lambda_function_name})

        # Build the list of enabled Profiling features and send along as a tag
        profiler_config = config_str(profiling_config)
        self.tags.update({"profiler_config": profiler_config})

        endpoint_call_counter_span_processor = self.tracer._endpoint_call_counter_span_processor
        if self.endpoint_collection_enabled:
            endpoint_call_counter_span_processor.enable()

        ddup.config(
            env=self.env,
            service=self.service,
            version=self.version,
            tags=cast(Mapping[Union[str, bytes], Union[str, bytes]], self.tags),
            max_nframes=profiling_config.max_frames,
            timeline_enabled=profiling_config.timeline_enabled,
            output_filename=profiling_config.output_pprof,
            sample_pool_capacity=profiling_config.sample_pool_capacity,
            timeout=profiling_config.api_timeout_ms,
            process_tags=self.process_tags,
        )
        ddup.start()

        # Surface the effective profiler configuration on each uploaded profile
        # under the event's `info.profiler.settings` header. This is a one-shot
        # snapshot at startup; runtime-mutable values (e.g. the adaptive
        # sampling interval) are already exposed via ProfilerStats fields.
        try:
            settings = profiling_config.dump_settings()
            # Drop `tags`: user/process tags already ride on the upload event's
            # dedicated tag channel and would otherwise be duplicated into
            # `info.profiler.settings.tags.*` for no extra signal.
            settings.pop("tags", None)
            info_payload = {"profiler": {"settings": settings}}
            ddup.set_profiler_settings_json(json.dumps(info_payload))
        except Exception:
            LOG.debug("Failed to publish profiler settings to info channel", exc_info=True)

    def __post_init__(self) -> None:
        if self._exception_profiling_enabled:
            LOG.debug("Profiling collector (exception) enabled")
            try:
                self._collectors.append(exception.ExceptionCollector())
                LOG.debug("Profiling collector (exception) initialized")
            except Exception:
                LOG.error("Failed to start exception collector, disabling.", exc_info=True)

        if self._stack_collector_enabled:
            LOG.debug("Profiling collector (stack) enabled")
            try:
                self._collectors.append(stack.StackCollector(tracer=self.tracer))
                LOG.debug("Profiling collector (stack) initialized")
            except Exception:
                LOG.error("Failed to start stack collector, disabling.", exc_info=True)

        if self._lock_collector_enabled:
            # These collectors require the import of modules, so we create them
            # if their import is detected at runtime.
            def start_lock_collector(collector_class: type[collector.Collector]) -> None:
                with self._service_lock:
                    if self._has_collector_type(collector_class):
                        return
                    col = collector_class(tracer=self.tracer)

                    if self.status == service.ServiceStatus.RUNNING:
                        # The profiler is already running so we need to start the collector
                        try:
                            col.start()
                            LOG.debug("Started collector %r", col)
                        except collector.CollectorUnavailable:
                            LOG.debug("Collector %r is unavailable, disabling", col)
                            if self._rollback_collector_start(col) is not None:
                                self._collectors_pending_cleanup.append(col)
                            return
                        except Exception:
                            LOG.error("Failed to start collector %r, disabling.", col, exc_info=True)
                            if self._rollback_collector_start(col) is not None:
                                self._collectors_pending_cleanup.append(col)
                            return
                        except BaseException:
                            if self._rollback_collector_start(col) is not None:
                                self._collectors_pending_cleanup.append(col)
                            raise

                    self._collectors.append(col)

            self._collectors_on_import.extend(
                [
                    ("threading", lambda _: start_lock_collector(threading.ThreadingLockCollector)),
                    ("threading", lambda _: start_lock_collector(threading.ThreadingRLockCollector)),
                    ("threading", lambda _: start_lock_collector(threading.ThreadingSemaphoreCollector)),
                    ("threading", lambda _: start_lock_collector(threading.ThreadingBoundedSemaphoreCollector)),
                    ("threading", lambda _: start_lock_collector(threading.ThreadingConditionCollector)),
                    ("asyncio", lambda _: start_lock_collector(asyncio.AsyncioLockCollector)),
                    ("asyncio", lambda _: start_lock_collector(asyncio.AsyncioSemaphoreCollector)),
                    ("asyncio", lambda _: start_lock_collector(asyncio.AsyncioBoundedSemaphoreCollector)),
                    ("asyncio", lambda _: start_lock_collector(asyncio.AsyncioConditionCollector)),
                ]
            )

        if self._pytorch_collector_enabled:

            def start_pytorch_collector(collector_class: type[collector.Collector]) -> None:
                with self._service_lock:
                    if self._has_collector_type(collector_class):
                        return
                    col = collector_class()

                    if self.status == service.ServiceStatus.RUNNING:
                        # The profiler is already running so we need to start the collector
                        try:
                            col.start()
                            LOG.debug("Started pytorch collector %r", col)
                        except collector.CollectorUnavailable:
                            LOG.debug("Collector %r pytorch is unavailable, disabling", col)
                            if self._rollback_collector_start(col) is not None:
                                self._collectors_pending_cleanup.append(col)
                            return
                        except Exception:
                            LOG.error("Failed to start collector %r pytorch, disabling.", col, exc_info=True)
                            if self._rollback_collector_start(col) is not None:
                                self._collectors_pending_cleanup.append(col)
                            return
                        except BaseException:
                            if self._rollback_collector_start(col) is not None:
                                self._collectors_pending_cleanup.append(col)
                            raise

                    self._collectors.append(col)

            self._collectors_on_import.append(
                ("torch", lambda _: start_pytorch_collector(pytorch.TorchProfilerCollector))
            )

        self._register_import_hooks()

        if self._memory_collector_enabled:
            self._collectors.append(memalloc.MemoryCollector())

        self._build_default_exporters()

        scheduler_class: type[Union[scheduler.Scheduler, scheduler.ServerlessScheduler]] = (
            scheduler.ServerlessScheduler if self._lambda_function_name else scheduler.Scheduler
        )

        self._scheduler = scheduler_class(
            before_flush=self._collectors_snapshot,
            tracer=self.tracer,
        )

    def _collectors_snapshot(self) -> None:
        for c in self._collectors:
            try:
                c.snapshot()
            except Exception:
                LOG.error("Error while snapshotting collector %r", c, exc_info=True)

    _COPY_IGNORE_ATTRIBUTES = {"status", "process_tags"}

    def copy(self) -> "_ProfilerInstance":
        return self.__class__(
            **{
                key: value
                for key, value in vars(self).items()
                if not key.startswith("_") and key not in self._COPY_IGNORE_ATTRIBUTES
            }
        )

    def start(self, *args: Any, **kwargs: Any) -> None:
        with self._service_lock:
            if self._start_cleanup_pending:
                self._stop_service(flush=False, join=True, _partial_start=True)
                self.status = service.ServiceStatus.STOPPED
                self._start_cleanup_pending = False
            if self.status == service.ServiceStatus.RUNNING:
                raise service.ServiceStatusError(self.__class__, self.status)

        self._register_import_hooks()
        super().start(*args, **kwargs)

    def _has_collector_type(self, collector_class: type[collector.Collector]) -> bool:
        if any(type(col) is collector_class for col in self._collectors):
            return True
        return self.status == service.ServiceStatus.RUNNING and any(
            type(col) is collector_class for col in self._collectors_pending_cleanup
        )

    def _start_service(self) -> None:
        """Start the profiler."""
        cleanup_errors = self._cleanup_pending_collectors()
        if cleanup_errors:
            raise cleanup_errors[0]
        self._stop_flush_attempted = False

        # See DD_PROFILING_NATIVE_HEAP_ENABLED. install() is permanent; children
        # inherit the patched GOT (and the activator skips a redundant re-install).
        # libdatadog may still refuse the patch via DD_HEAP_SAMPLING_ENABLED
        # (unset = on); that is not a ddtrace setting — see heap_gotter docs.
        if profiling_config.native_heap.enabled:
            from ddtrace.internal.datadog.profiling import heap_gotter

            try:
                if heap_gotter.install():
                    mode: str = "live-heap" if heap_gotter.live_heap_enabled() else "allocation-only"
                    LOG.info("Native heap profiling armed (GOT overrides installed, %s)", mode)
                else:
                    LOG.warning("Native heap profiling requested but GOT overrides were not installed")
            except Exception:
                LOG.error("Failed to arm native heap profiling", exc_info=True)

        collectors_to_start = self._collectors
        collectors: list[collector.Collector | memalloc.MemoryCollector] = []
        for index, col in enumerate(collectors_to_start):
            try:
                col.start()
            except collector.CollectorUnavailable:
                LOG.debug("Collector %r is unavailable, disabling", col)
                cleanup_error = self._rollback_collector_start(col)
                if cleanup_error is not None:
                    self._collectors = collectors + collectors_to_start[index + 1 :]
                    self._collectors_pending_cleanup.append(col)
                    raise cleanup_error
            except Exception:
                LOG.error("Failed to start collector %r, disabling.", col, exc_info=True)
                cleanup_error = self._rollback_collector_start(col)
                if cleanup_error is not None:
                    self._collectors = collectors + collectors_to_start[index + 1 :]
                    self._collectors_pending_cleanup.append(col)
                    raise cleanup_error
            except BaseException:
                if self._rollback_collector_start(col) is not None:
                    self._collectors_pending_cleanup.append(col)
                self._collectors = collectors + collectors_to_start[index:]
                raise
            else:
                collectors.append(col)
        self._collectors = collectors

        if self._scheduler is not None:
            self._scheduler.start()

    @staticmethod
    def _rollback_collector_start(
        col: Union[collector.Collector, memalloc.MemoryCollector],
    ) -> Optional[BaseException]:
        try:
            col._rollback_start()
        except BaseException as error:
            LOG.debug("Failed to clean up partially started collector %r", col, exc_info=True)
            return error
        return None

    def _cleanup_pending_collectors(self) -> list[BaseException]:
        errors: list[BaseException] = []
        collectors_pending_cleanup: list[collector.Collector | memalloc.MemoryCollector] = []
        for col in self._collectors_pending_cleanup:
            cleanup_error = self._rollback_collector_start(col)
            if cleanup_error is not None:
                collectors_pending_cleanup.append(col)
                errors.append(cleanup_error)
        self._collectors_pending_cleanup = collectors_pending_cleanup
        return errors

    def _rollback_start(self, flush: bool = True, join: bool = True) -> None:
        with self._service_lock:
            self._start_cleanup_pending = True
            self._start_cleanup_generation = forksafe.get_generation()
            try:
                self._stop_service(flush=flush, join=join, _partial_start=True)
            except BaseException:
                raise
            self.status = service.ServiceStatus.STOPPED
            self._start_cleanup_pending = False

    def _register_import_hooks(self) -> None:
        for module, hook in self._collectors_on_import:
            if (module, hook) not in self._collectors_on_import_registered:
                self._collectors_on_import_registered.append((module, hook))
                ModuleWatchdog.register_module_hook(module, hook)

    def _unregister_import_hooks(self) -> list[BaseException]:
        errors: list[BaseException] = []
        hooks_pending_cleanup: list[tuple[str, Callable[[Any], None]]] = []
        for module, hook in self._collectors_on_import_registered:
            try:
                ModuleWatchdog.unregister_module_hook(module, hook)
            except BaseException as error:
                errors.append(error)
                hooks_pending_cleanup.append((module, hook))
        self._collectors_on_import_registered = hooks_pending_cleanup
        return errors

    def _stop_service(self, flush: bool = True, join: bool = True, _partial_start: bool = False) -> None:
        """Stop the profiler.

        :param flush: Flush a last profile.
        """
        LOG.debug("Stopping profiler")

        errors = self._unregister_import_hooks()

        if self._scheduler is not None:
            scheduler_stopped = (
                self.status == service.ServiceStatus.RUNNING and self._scheduler.status == service.ServiceStatus.STOPPED
            )
            if not scheduler_stopped:
                try:
                    if _partial_start:
                        self._scheduler._rollback_start()
                    else:
                        self._scheduler.stop()
                except BaseException as error:
                    errors.append(error)
                else:
                    scheduler_stopped = True
            if scheduler_stopped:
                # Wait for the export to be over: export might need collectors (e.g., for snapshot) so we can't stop
                # collectors before the possibly running flush is finished.
                if join:
                    try:
                        self._scheduler.join()
                    except BaseException as error:
                        errors.append(error)
                        scheduler_stopped = False
            if not scheduler_stopped:
                raise errors[0]
            if flush and not self._stop_flush_attempted:
                # Do not stop the collectors before flushing, they might be needed (snapshot)
                self._stop_flush_attempted = True
                try:
                    self._scheduler.flush()
                except BaseException as error:
                    errors.append(error)

        errors.extend(self._cleanup_pending_collectors())

        collectors_stopped: list[collector.Collector | memalloc.MemoryCollector] = []
        for col in reversed(self._collectors):
            try:
                if (
                    _partial_start
                    and isinstance(col, collector.Collector)
                    and col.status == service.ServiceStatus.STOPPED
                ):
                    collectors_stopped.append(col)
                    continue
                col.stop()
            except service.ServiceStatusError:
                # It's possible some collector failed to start, ignore failure to stop
                collectors_stopped.append(col)
            except BaseException as error:
                errors.append(error)
            else:
                collectors_stopped.append(col)

        if join:
            for col in collectors_stopped:
                try:
                    col.join()
                except BaseException as error:
                    errors.append(error)

        if errors:
            raise errors[0]
