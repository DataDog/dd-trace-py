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
from ddtrace.profiling.collector import gc as gc_collector
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
            active = Profiler._active_instance
            if active is not None and active is not self and active._profiler.status == service.ServiceStatus.RUNNING:
                LOG.error(
                    "A profiler is already running. Only one profiler instance can be active at a time. "
                    "The second profiler will not be started."
                )
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

            self._profiler.start()
            Profiler._active_instance = self

        atexit.register(self.stop)

        # register_on_exit_signal is needed for processes terminated via SIGTERM (e.g.
        # Ray workers, Kubernetes pods). Python atexit handlers do NOT run on SIGTERM by default,
        # so without this the last partial profile window is silently lost.
        # We register _stop_on_signal (not stop) to avoid deadlocking when SIGTERM arrives while
        # _active_lock is already held by the main thread (e.g. during start or stop).
        atexit.register_on_exit_signal(self._stop_on_signal)

        # Note: For regular fork(), native pthread_atfork handlers restart the sampling thread
        # and PeriodicThread auto-restart handles the Scheduler. No explicit forksafe hook needed.
        # For uWSGI, _start_on_fork is registered via uwsgidecorators.postfork() in check_uwsgi().

        telemetry_writer.product_activated(TELEMETRY_APM_PRODUCT.PROFILER, True)

    def stop(self, flush: bool = True) -> None:
        """Stop the profiler.

        :param flush: Flush last profile.
        """
        atexit.unregister(self.stop)
        try:
            with Profiler._active_lock:
                self._profiler.stop(flush)
                if Profiler._active_instance is self:
                    Profiler._active_instance = None
            telemetry_writer.product_activated(TELEMETRY_APM_PRODUCT.PROFILER, False)
        except service.ServiceStatusError:
            # Not a best practice, but for backward API compatibility that allowed to call `stop` multiple times.
            pass

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
            self._profiler.stop(flush=True)
        except service.ServiceStatusError:
            pass
        except Exception:
            LOG.debug("Exception while stopping profiler on exit signal", exc_info=True)
        finally:
            if Profiler._active_instance is self:
                Profiler._active_instance = None
            telemetry_writer.product_activated(TELEMETRY_APM_PRODUCT.PROFILER, False)
            Profiler._active_lock.release()

    def _start_on_fork(self) -> None:
        """Start a fresh profiler in child process after fork. This is needed for uWSGI support."""
        with Profiler._active_lock:
            if Profiler._active_instance is not None:
                LOG.error(
                    "A profiler is already running. Only one profiler instance can be active at a time. "
                    "The second profiler will not be started."
                )
                return

            self._profiler.start()
            Profiler._active_instance = self

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
        _gc_collector_enabled: bool = profiling_config.gc.enabled,
        _pytorch_collector_enabled: bool = profiling_config.pytorch.enabled,
        _exception_profiling_enabled: bool = profiling_config.exception.enabled,
        enable_code_provenance: bool = profiling_config.code_provenance,
        endpoint_collection_enabled: bool = profiling_config.endpoint_collection,
    ):
        super().__init__()
        # User-supplied values
        self.service: Optional[str] = service if service is not None else config.service
        self.tags: dict[str, str] = tags if tags is not None else profiling_config.tags
        self.env: Optional[str] = env if env is not None else config.env
        self.version: Optional[str] = version if version is not None else config.version
        self.tracer: Any = tracer
        self.api_key: Optional[str] = api_key if api_key is not None else config._dd_api_key
        self._memory_collector_enabled: bool = _memory_collector_enabled
        self._stack_collector_enabled: bool = _stack_collector_enabled
        self._lock_collector_enabled: bool = _lock_collector_enabled
        self._gc_collector_enabled: bool = _gc_collector_enabled
        self._pytorch_collector_enabled: bool = _pytorch_collector_enabled
        self._exception_profiling_enabled: bool = _exception_profiling_enabled
        self.enable_code_provenance: bool = enable_code_provenance
        self.endpoint_collection_enabled: bool = endpoint_collection_enabled

        # Non-user-supplied values
        # Note: memalloc.MemoryCollector is not a subclass of collector.Collector, so we need to use a union type.
        #       This is because its snapshot method cannot be static.
        self._collectors: list[collector.Collector | memalloc.MemoryCollector] = []
        self._collectors_on_import: Optional[list[tuple[str, Callable[[Any], None]]]] = None
        self._scheduler: Optional[Union[scheduler.Scheduler, scheduler.ServerlessScheduler]] = None
        self._lambda_function_name: Optional[str] = _env.get("AWS_LAMBDA_FUNCTION_NAME")

        self.process_tags: Optional[str] = process_tags.process_tags or None

        self.__post_init__()

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

        if self._gc_collector_enabled:
            LOG.debug("Profiling collector (gc) enabled")
            try:
                self._collectors.append(gc_collector.GCCollector())
                LOG.debug("Profiling collector (gc) initialized")
            except Exception:
                LOG.error("Failed to start gc collector, disabling.", exc_info=True)

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
            def start_collector(collector_class: type[collector.Collector]) -> None:
                with self._service_lock:
                    if any(type(c) is collector_class for c in self._collectors):
                        return
                    col = collector_class(tracer=self.tracer)

                    if self.status == service.ServiceStatus.RUNNING:
                        # The profiler is already running so we need to start the collector
                        try:
                            col.start()
                            LOG.debug("Started collector %r", col)
                        except collector.CollectorUnavailable:
                            LOG.debug("Collector %r is unavailable, disabling", col)
                            return
                        except Exception:
                            LOG.error("Failed to start collector %r, disabling.", col, exc_info=True)
                            return

                    self._collectors.append(col)

            self._collectors_on_import = [
                ("threading", lambda _: start_collector(threading.ThreadingLockCollector)),
                ("threading", lambda _: start_collector(threading.ThreadingRLockCollector)),
                ("threading", lambda _: start_collector(threading.ThreadingSemaphoreCollector)),
                ("threading", lambda _: start_collector(threading.ThreadingBoundedSemaphoreCollector)),
                ("threading", lambda _: start_collector(threading.ThreadingConditionCollector)),
                ("asyncio", lambda _: start_collector(asyncio.AsyncioLockCollector)),
                ("asyncio", lambda _: start_collector(asyncio.AsyncioSemaphoreCollector)),
                ("asyncio", lambda _: start_collector(asyncio.AsyncioBoundedSemaphoreCollector)),
                ("asyncio", lambda _: start_collector(asyncio.AsyncioConditionCollector)),
            ]

            for module, hook in self._collectors_on_import:
                ModuleWatchdog.register_module_hook(module, hook)

        if self._pytorch_collector_enabled:

            def start_collector(collector_class: type[collector.Collector]) -> None:
                with self._service_lock:
                    if any(type(c) is collector_class for c in self._collectors):
                        return
                    col = collector_class()

                    if self.status == service.ServiceStatus.RUNNING:
                        # The profiler is already running so we need to start the collector
                        try:
                            col.start()
                            LOG.debug("Started pytorch collector %r", col)
                        except collector.CollectorUnavailable:
                            LOG.debug("Collector %r pytorch is unavailable, disabling", col)
                            return
                        except Exception:
                            LOG.error("Failed to start collector %r pytorch, disabling.", col, exc_info=True)
                            return

                    self._collectors.append(col)

            if self._collectors_on_import is None:
                self._collectors_on_import = []

            torch_hooks: list[tuple[str, Callable[[Any], None]]] = [
                ("torch", lambda _: start_collector(pytorch.TorchProfilerCollector)),
            ]
            self._collectors_on_import.extend(torch_hooks)

            for module, hook in torch_hooks:
                ModuleWatchdog.register_module_hook(module, hook)

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

    def _start_service(self) -> None:
        """Start the profiler."""
        collectors = []
        for col in self._collectors:
            try:
                col.start()
            except collector.CollectorUnavailable:
                LOG.debug("Collector %r is unavailable, disabling", col)
            except Exception:
                LOG.error("Failed to start collector %r, disabling.", col, exc_info=True)
            else:
                collectors.append(col)
        self._collectors = collectors

        if self._scheduler is not None:
            self._scheduler.start()

    def _stop_service(self, flush: bool = True, join: bool = True) -> None:
        """Stop the profiler.

        :param flush: Flush a last profile.
        """
        LOG.debug("Stopping profiler")

        # Prevent doing more initialisation now that we are shutting down.
        if self._collectors_on_import:
            for module, hook in self._collectors_on_import:
                ModuleWatchdog.unregister_module_hook(module, hook)
            self._collectors_on_import = None

        if self._scheduler is not None:
            self._scheduler.stop()
            # Wait for the export to be over: export might need collectors (e.g., for snapshot) so we can't stop
            # collectors before the possibly running flush is finished.
            if join:
                self._scheduler.join()
            if flush:
                # Do not stop the collectors before flushing, they might be needed (snapshot)
                self._scheduler.flush()

        for col in reversed(self._collectors):
            try:
                col.stop()
            except service.ServiceStatusError:
                # It's possible some collector failed to start, ignore failure to stop
                pass

        if join:
            for col in reversed(self._collectors):
                col.join()
