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
        # Per-instance shutdown coordination. Both `stop()` (atexit, explicit call) and
        # `_stop_on_signal` (SIGTERM/SIGINT) funnel through the same idempotency guard so
        # the last profile is flushed exactly once, regardless of which exit path fires
        # first or whether several paths race. Mirrors the pattern in `Tracer.shutdown`.
        self._shutdown_lock = Lock()
        self._did_shutdown: bool = False

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
        import os as _os, sys as _sys  # DEBUG
        print(f"[DD-DEBUG] pid={_os.getpid()} Profiler.start() REGISTERED id(self)={id(self)}", file=_sys.stderr, flush=True)  # DEBUG

        # Note: For regular fork(), native pthread_atfork handlers restart the sampling thread
        # and PeriodicThread auto-restart handles the Scheduler. No explicit forksafe hook needed.
        # For uWSGI, _start_on_fork is registered via uwsgidecorators.postfork() in check_uwsgi().

        telemetry_writer.product_activated(TELEMETRY_APM_PRODUCT.PROFILER, True)

    def stop(self, flush: bool = True) -> None:
        """Stop the profiler.

        :param flush: Flush last profile.

        Idempotent: subsequent calls (including races with the SIGTERM/SIGINT handler and
        atexit) are no-ops. For backward API compatibility, `stop()` previously allowed
        repeated calls via a ServiceStatusError catch; the per-instance shutdown guard
        here is strictly stronger and also dedupes against the exit-signal path added in
        #18041.
        """
        import os, sys  # DEBUG
        print(f"[DD-DEBUG] pid={os.getpid()} Profiler.stop() ENTRY id(self)={id(self)} flush={flush}", file=sys.stderr, flush=True)  # DEBUG
        if not self._shutdown_lock.acquire(blocking=True):
            print(f"[DD-DEBUG] pid={os.getpid()} Profiler.stop() lock-fail", file=sys.stderr, flush=True)  # DEBUG
            return
        try:
            if self._did_shutdown:
                print(f"[DD-DEBUG] pid={os.getpid()} Profiler.stop() ALREADY-SHUTDOWN id(self)={id(self)}", file=sys.stderr, flush=True)  # DEBUG
                return
            self._did_shutdown = True
            atexit.unregister(self.stop)
            try:
                with Profiler._active_lock:
                    print(f"[DD-DEBUG] pid={os.getpid()} Profiler.stop() FLUSHING id(self)={id(self)}", file=sys.stderr, flush=True)  # DEBUG
                    self._profiler.stop(flush)
                    if Profiler._active_instance is self:
                        Profiler._active_instance = None
                telemetry_writer.product_activated(TELEMETRY_APM_PRODUCT.PROFILER, False)
            except service.ServiceStatusError:
                # Underlying profiler instance is already stopped (e.g. user called
                # _profiler.stop directly). Treat as success.
                print(f"[DD-DEBUG] pid={os.getpid()} Profiler.stop() ServiceStatusError id(self)={id(self)}", file=sys.stderr, flush=True)  # DEBUG
                pass
        finally:
            self._shutdown_lock.release()

    def _stop_on_signal(self) -> None:
        """Flush and stop the profiler when an exit signal (SIGTERM/SIGINT) is received.

        Signal handlers run in the main thread between bytecodes. To stay deadlock-free,
        we use *non-blocking* lock acquires throughout:

        * `_shutdown_lock` (instance-level): dedup against `stop()` running on another
          thread. If we can't grab it, shutdown is in progress and will complete the flush;
          we bail out so we don't double-flush.
        * `_active_lock` (class-level): same concern, plus avoids re-entrant deadlock if
          SIGTERM arrives while the main thread is inside `start()`/`stop()` and already
          holds the lock.

        The combined `_shutdown_lock` + `_did_shutdown` flag is the source of truth for
        "flush happened once"; it dedupes between atexit (`stop`) and signal
        (`_stop_on_signal`) regardless of which path wins the race, fixing the
        double-flush observed when uvicorn worker shutdown exercises both paths.
        """
        import os, sys  # DEBUG
        print(f"[DD-DEBUG] pid={os.getpid()} _stop_on_signal() ENTRY id(self)={id(self)}", file=sys.stderr, flush=True)  # DEBUG
        if not self._shutdown_lock.acquire(blocking=False):
            # Shutdown is already running on another thread (or this same thread held
            # the lock when the signal fired). The in-progress shutdown will flush.
            print(f"[DD-DEBUG] pid={os.getpid()} _stop_on_signal() lock-fail", file=sys.stderr, flush=True)  # DEBUG
            return
        try:
            if self._did_shutdown:
                print(f"[DD-DEBUG] pid={os.getpid()} _stop_on_signal() ALREADY-SHUTDOWN id(self)={id(self)}", file=sys.stderr, flush=True)  # DEBUG
                return
            if not Profiler._active_lock.acquire(blocking=False):
                print(f"[DD-DEBUG] pid={os.getpid()} _stop_on_signal() active-lock-fail", file=sys.stderr, flush=True)  # DEBUG
                # `start()`/`stop()` is in progress on the main thread between bytecodes.
                # We're now running in that same thread via the signal handler; a blocking
                # acquire would deadlock. The narrow race where `_raise_default`
                # terminates the process before the in-progress stop completes is an
                # accepted limitation (same as the pre-fix code).
                return
            self._did_shutdown = True
            atexit.unregister(self.stop)
            try:
                print(f"[DD-DEBUG] pid={os.getpid()} _stop_on_signal() FLUSHING id(self)={id(self)}", file=sys.stderr, flush=True)  # DEBUG
                self._profiler.stop(flush=True)
            except service.ServiceStatusError:
                print(f"[DD-DEBUG] pid={os.getpid()} _stop_on_signal() ServiceStatusError id(self)={id(self)}", file=sys.stderr, flush=True)  # DEBUG
                pass
            except Exception:
                LOG.debug("Exception while stopping profiler on exit signal", exc_info=True)
            finally:
                if Profiler._active_instance is self:
                    Profiler._active_instance = None
                telemetry_writer.product_activated(TELEMETRY_APM_PRODUCT.PROFILER, False)
                Profiler._active_lock.release()
        finally:
            self._shutdown_lock.release()

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
    """A instance of the profiler.

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
        self.tags: dict[str, str] = tags if tags is not None else profiling_config.tags
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
