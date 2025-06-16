# -*- encoding: utf-8 -*-
import logging
import os
from typing import Any
from typing import Dict
from typing import List  # noqa:F401
from typing import Optional  # noqa:F401
from typing import Type  # noqa:F401
from typing import Union  # noqa:F401

import ddtrace
from ddtrace import config
from ddtrace.internal import atexit
from ddtrace.internal import forksafe
from ddtrace.internal import service
from ddtrace.internal import uwsgi
from ddtrace.internal.datadog.profiling import ddup
from ddtrace.internal.module import ModuleWatchdog
from ddtrace.internal.telemetry import telemetry_writer
from ddtrace.internal.telemetry.constants import TELEMETRY_APM_PRODUCT
from ddtrace.profiling import collector
from ddtrace.profiling import scheduler
from ddtrace.profiling.collector import asyncio
from ddtrace.profiling.collector import memalloc
from ddtrace.profiling.collector import pytorch
from ddtrace.profiling.collector import stack
from ddtrace.profiling.collector import threading
from ddtrace.settings.profiling import config as profiling_config
from ddtrace.settings.profiling import config_str


LOG = logging.getLogger(__name__)


class Profiler(object):
    """Run profiling while code is executed.

    Note that the whole Python process is profiled, not only the code executed. Data from all running threads are
    caught.

    """

    def __init__(self, *args, **kwargs):
        self._profiler = _ProfilerInstance(*args, **kwargs)

    def start(self, stop_on_exit=True, profile_children=True):
        """Start the profiler.

        :param stop_on_exit: Whether to stop the profiler and flush the profile on exit.
        :param profile_children: Whether to start a profiler in child processes.
        """

        if profile_children:
            try:
                uwsgi.check_uwsgi(self._restart_on_fork, atexit=self.stop if stop_on_exit else None)
            except uwsgi.uWSGIMasterProcess:
                # Do nothing, the start() method will be called in each worker subprocess
                return

        self._profiler.start()

        if stop_on_exit:
            atexit.register(self.stop)

        if profile_children:
            forksafe.register(self._restart_on_fork)

        telemetry_writer.product_activated(TELEMETRY_APM_PRODUCT.PROFILER, True)

    def stop(self, flush=True):
        """Stop the profiler.

        :param flush: Flush last profile.
        """
        atexit.unregister(self.stop)
        try:
            self._profiler.stop(flush)
            telemetry_writer.product_activated(TELEMETRY_APM_PRODUCT.PROFILER, False)
        except service.ServiceStatusError:
            # Not a best practice, but for backward API compatibility that allowed to call `stop` multiple times.
            pass

    def _restart_on_fork(self):
        # Be sure to stop the parent first, since it might have to e.g. unpatch functions
        # Do not flush data as we don't want to have multiple copies of the parent profile exported.
        try:
            self._profiler.stop(flush=False, join=False)
        except service.ServiceStatusError:
            # This can happen in uWSGI mode: the children won't have the _profiler started from the master process
            pass
        self._profiler = self._profiler.copy()
        self._profiler.start()

    def __getattr__(
        self,
        key,  # type: str
    ):
        # type: (...) -> Any
        return getattr(self._profiler, key)


class _ProfilerInstance(service.Service):
    """A instance of the profiler.

    Each process must manage its own instance.

    """

    def __init__(
        self,
        service: Optional[str] = None,
        tags: Optional[Dict[str, str]] = None,
        env: Optional[str] = None,
        version: Optional[str] = None,
        tracer: Any = ddtrace.tracer,
        api_key: Optional[str] = None,
        _memory_collector_enabled: bool = profiling_config.memory.enabled,
        _stack_collector_enabled: bool = profiling_config.stack.enabled,
        _stack_v2_enabled: bool = profiling_config.stack.v2_enabled,
        _lock_collector_enabled: bool = profiling_config.lock.enabled,
        _pytorch_collector_enabled: bool = profiling_config.pytorch.enabled,
        enable_code_provenance: bool = profiling_config.code_provenance,
        endpoint_collection_enabled: bool = profiling_config.endpoint_collection,
    ):
        super().__init__()
        # User-supplied values
        self.service: Optional[str] = service if service is not None else config.service
        self.tags: Dict[str, str] = tags if tags is not None else profiling_config.tags
        self.env: Optional[str] = env if env is not None else config.env
        self.version: Optional[str] = version if version is not None else config.version
        self.tracer: Any = tracer
        self.api_key: Optional[str] = api_key if api_key is not None else config._dd_api_key
        self._memory_collector_enabled: bool = _memory_collector_enabled
        self._stack_collector_enabled: bool = _stack_collector_enabled
        self._stack_v2_enabled: bool = _stack_v2_enabled
        self._lock_collector_enabled: bool = _lock_collector_enabled
        self._pytorch_collector_enabled: bool = _pytorch_collector_enabled
        self.enable_code_provenance: bool = enable_code_provenance
        self.endpoint_collection_enabled: bool = endpoint_collection_enabled

        # Non-user-supplied values
        self._collectors: List[Union[stack.StackCollector, memalloc.MemoryCollector]] = []
        self._collectors_on_import: Any = None
        self._scheduler: Optional[Union[scheduler.Scheduler, scheduler.ServerlessScheduler]] = None
        self._lambda_function_name: Optional[str] = os.environ.get("AWS_LAMBDA_FUNCTION_NAME")

        self.__post_init__()

    def __eq__(self, other):
        for k, v in vars(self).items():
            if k.startswith("_") or k in self._COPY_IGNORE_ATTRIBUTES:
                continue
            if v != getattr(other, k, None):
                return False
        return True

    def _build_default_exporters(self):
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
            tags=self.tags,
            max_nframes=profiling_config.max_frames,
            timeline_enabled=profiling_config.timeline_enabled,
            output_filename=profiling_config.output_pprof,
            sample_pool_capacity=profiling_config.sample_pool_capacity,
        )
        ddup.start()

    def __post_init__(self):
        # type: (...) -> None

        if self._stack_collector_enabled:
            LOG.debug("Profiling collector (stack) enabled")
            try:
                self._collectors.append(
                    stack.StackCollector(
                        tracer=self.tracer,
                        endpoint_collection_enabled=self.endpoint_collection_enabled,
                    )
                )
                LOG.debug("Profiling collector (stack) initialized")
            except Exception:
                LOG.error("Failed to start stack collector, disabling.", exc_info=True)

        if self._lock_collector_enabled:
            # These collectors require the import of modules, so we create them
            # if their import is detected at runtime.
            def start_collector(collector_class: Type) -> None:
                with self._service_lock:
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
                ("asyncio", lambda _: start_collector(asyncio.AsyncioLockCollector)),
            ]

            for module, hook in self._collectors_on_import:
                ModuleWatchdog.register_module_hook(module, hook)

        if self._pytorch_collector_enabled:

            def start_collector(collector_class: Type) -> None:
                with self._service_lock:
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

            self._collectors_on_import = [
                ("torch", lambda _: start_collector(pytorch.TorchProfilerCollector)),
            ]

            for module, hook in self._collectors_on_import:
                ModuleWatchdog.register_module_hook(module, hook)

        if self._memory_collector_enabled:
            self._collectors.append(memalloc.MemoryCollector())

        self._build_default_exporters()

        scheduler_class = (
            scheduler.ServerlessScheduler if self._lambda_function_name else scheduler.Scheduler
        )  # type: (Type[Union[scheduler.Scheduler, scheduler.ServerlessScheduler]])

        self._scheduler = scheduler_class(
            before_flush=self._collectors_snapshot,
            tracer=self.tracer,
        )

    def _collectors_snapshot(self):
        for c in self._collectors:
            try:
                c.snapshot()
            except Exception:
                LOG.error("Error while snapshotting collector %r", c, exc_info=True)

    _COPY_IGNORE_ATTRIBUTES = {"status"}

    def copy(self):
        return self.__class__(
            **{
                key: value
                for key, value in vars(self).items()
                if not key.startswith("_") and key not in self._COPY_IGNORE_ATTRIBUTES
            }
        )

    def _start_service(self):
        # type: (...) -> None
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

    def _stop_service(self, flush=True, join=True):
        # type: (bool, bool) -> None
        """Stop the profiler.

        :param flush: Flush a last profile.
        """
        # Prevent doing more initialisation now that we are shutting down.
        if self._lock_collector_enabled:
            for module, hook in self._collectors_on_import:
                try:
                    ModuleWatchdog.unregister_module_hook(module, hook)
                except ValueError:
                    pass

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
