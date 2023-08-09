# -*- encoding: utf-8 -*-
import logging
import os
import typing
from typing import List
from typing import Optional
from typing import Type
from typing import Union

import attr

import ddtrace
from ddtrace.internal import agent
from ddtrace.internal import atexit
from ddtrace.internal import forksafe
from ddtrace.internal import service
from ddtrace.internal import uwsgi
from ddtrace.internal import writer
from ddtrace.internal.datadog.profiling import ddup
from ddtrace.internal.module import ModuleWatchdog
from ddtrace.profiling import collector
from ddtrace.profiling import exporter
from ddtrace.profiling import recorder
from ddtrace.profiling import scheduler
from ddtrace.profiling.collector import asyncio
from ddtrace.profiling.collector import memalloc
from ddtrace.profiling.collector import stack
from ddtrace.profiling.collector import stack_event
from ddtrace.profiling.collector import threading
from ddtrace.profiling.exporter import file
from ddtrace.profiling.exporter import http
from ddtrace.settings.profiling import config

from . import _asyncio


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

    def stop(self, flush=True):
        """Stop the profiler.

        :param flush: Flush last profile.
        """
        atexit.unregister(self.stop)
        try:
            self._profiler.stop(flush)
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
        self, key  # type: str
    ):
        # type: (...) -> typing.Any
        return getattr(self._profiler, key)


@attr.s
class _ProfilerInstance(service.Service):
    """A instance of the profiler.

    Each process must manage its own instance.

    """

    # User-supplied values
    url = attr.ib(default=None)
    service = attr.ib(factory=lambda: os.environ.get("DD_SERVICE"))
    tags = attr.ib(factory=dict, type=typing.Dict[str, str])
    env = attr.ib(factory=lambda: os.environ.get("DD_ENV"))
    version = attr.ib(factory=lambda: os.environ.get("DD_VERSION"))
    tracer = attr.ib(default=ddtrace.tracer)
    api_key = attr.ib(factory=lambda: os.environ.get("DD_API_KEY"), type=Optional[str])
    agentless = attr.ib(type=bool, default=config.agentless)
    _memory_collector_enabled = attr.ib(type=bool, default=config.memory.enabled)
    enable_code_provenance = attr.ib(type=bool, default=config.code_provenance)
    endpoint_collection_enabled = attr.ib(type=bool, default=config.endpoint_collection)

    _recorder = attr.ib(init=False, default=None)
    _collectors = attr.ib(init=False, default=None)
    _scheduler = attr.ib(init=False, default=None, type=Union[scheduler.Scheduler, scheduler.ServerlessScheduler])
    _lambda_function_name = attr.ib(
        init=False, factory=lambda: os.environ.get("AWS_LAMBDA_FUNCTION_NAME"), type=Optional[str]
    )
    _export_libdd_enabled = attr.ib(type=bool, default=config.export.libdd_enabled)
    _export_py_enabled = attr.ib(type=bool, default=config.export.py_enabled)

    ENDPOINT_TEMPLATE = "https://intake.profile.{}"

    def _build_default_exporters(self):
        # type: (...) -> List[exporter.Exporter]
        _OUTPUT_PPROF = config.output_pprof
        if _OUTPUT_PPROF:
            return [
                file.PprofFileExporter(prefix=_OUTPUT_PPROF),
            ]

        if self.url is not None:
            endpoint = self.url
        elif self.agentless:
            LOG.warning(
                "Agentless uploading is currently for internal usage only and not officially supported. "
                "You should not enable it unless somebody at Datadog instructed you to do so."
            )
            endpoint = self.ENDPOINT_TEMPLATE.format(os.environ.get("DD_SITE", "datadoghq.com"))
        else:
            if isinstance(self.tracer._writer, writer.AgentWriter):
                endpoint = self.tracer._writer.agent_url
            else:
                endpoint = agent.get_trace_url()

        if self.agentless:
            endpoint_path = "/api/v2/profile"
        else:
            # Agent mode
            # path is relative because it is appended
            # to the agent base path.
            endpoint_path = "profiling/v1/input"

        if self._lambda_function_name is not None:
            self.tags.update({"functionname": self._lambda_function_name})

        endpoint_call_counter_span_processor = self.tracer._endpoint_call_counter_span_processor
        if self.endpoint_collection_enabled:
            endpoint_call_counter_span_processor.enable()

        if self._export_libdd_enabled:
            versionname = (
                "{}.libdd".format(self.version)
                if self._export_py_enabled and self.version is not None
                else self.version
            )
            ddup.init(
                env=self.env,
                service=self.service,
                version=versionname,
                tags=self.tags,
                max_nframes=config.max_frames,
                url=endpoint,
            )

        if self._export_py_enabled:
            return [
                http.PprofHTTPExporter(
                    service=self.service,
                    env=self.env,
                    tags=self.tags,
                    version=self.version,
                    api_key=self.api_key,
                    endpoint=endpoint,
                    endpoint_path=endpoint_path,
                    enable_code_provenance=self.enable_code_provenance,
                    endpoint_call_counter_span_processor=endpoint_call_counter_span_processor,
                )
            ]
        return []

    def __attrs_post_init__(self):
        # type: (...) -> None
        # Allow to store up to 10 threads for 60 seconds at 50 Hz
        max_stack_events = 10 * 60 * 50
        r = self._recorder = recorder.Recorder(
            max_events={
                stack_event.StackSampleEvent: max_stack_events,
                stack_event.StackExceptionSampleEvent: int(max_stack_events / 2),
                # (default buffer size / interval) * export interval
                memalloc.MemoryAllocSampleEvent: int(
                    (memalloc.MemoryCollector._DEFAULT_MAX_EVENTS / memalloc.MemoryCollector._DEFAULT_INTERVAL) * 60
                ),
                # Do not limit the heap sample size as the number of events is relative to allocated memory anyway
                memalloc.MemoryHeapSampleEvent: None,
            },
            default_max_events=config.max_events,
        )

        self._collectors = [
            stack.StackCollector(
                r,
                tracer=self.tracer,
                endpoint_collection_enabled=self.endpoint_collection_enabled,
            ),  # type: ignore[call-arg]
            threading.ThreadingLockCollector(r, tracer=self.tracer),
        ]

        if _asyncio.is_asyncio_available():

            @ModuleWatchdog.after_module_imported("asyncio")
            def _(_):
                with self._service_lock:
                    col = asyncio.AsyncioLockCollector(r, tracer=self.tracer)

                    if self.status == service.ServiceStatus.RUNNING:
                        # The profiler is already running so we need to start the collector
                        try:
                            col.start()
                        except collector.CollectorUnavailable:
                            LOG.debug("Collector %r is unavailable, disabling", col)
                            return
                        except Exception:
                            LOG.error("Failed to start collector %r, disabling.", col, exc_info=True)
                            return

                    self._collectors.append(col)

        if self._memory_collector_enabled:
            self._collectors.append(memalloc.MemoryCollector(r))

        exporters = self._build_default_exporters()

        if exporters or self._export_libdd_enabled:
            scheduler_class = (
                scheduler.ServerlessScheduler if self._lambda_function_name else scheduler.Scheduler
            )  # type: (Type[Union[scheduler.Scheduler, scheduler.ServerlessScheduler]])

            self._scheduler = scheduler_class(
                recorder=r,
                exporters=exporters,
                before_flush=self._collectors_snapshot,
            )

    def _collectors_snapshot(self):
        for c in self._collectors:
            try:
                snapshot = c.snapshot()
                if snapshot:
                    for events in snapshot:
                        self._recorder.push_events(events)
            except Exception:
                LOG.error("Error while snapshoting collector %r", c, exc_info=True)

    _COPY_IGNORE_ATTRIBUTES = {"status"}

    def copy(self):
        return self.__class__(
            **{
                a.name: getattr(self, a.name)
                for a in attr.fields(self.__class__)
                if a.name[0] != "_" and a.name not in self._COPY_IGNORE_ATTRIBUTES
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

    def visible_events(self):
        return self._export_py_enabled
