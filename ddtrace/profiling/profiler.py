# -*- encoding: utf-8 -*-
import logging
import os
import typing
from typing import List
from typing import Optional

import attr

import ddtrace
from ddtrace.internal import agent
from ddtrace.internal import atexit
from ddtrace.internal import service
from ddtrace.internal import uwsgi
from ddtrace.internal import writer
from ddtrace.internal.utils import formats
from ddtrace.profiling import collector
from ddtrace.profiling import exporter
from ddtrace.profiling import recorder
from ddtrace.profiling import scheduler
from ddtrace.profiling.collector import memalloc
from ddtrace.profiling.collector import stack
from ddtrace.profiling.collector import stack_event
from ddtrace.profiling.collector import threading
from ddtrace.profiling.exporter import file
from ddtrace.profiling.exporter import http

from . import _asyncio
from ._asyncio import DdtraceProfilerEventLoopPolicy


LOG = logging.getLogger(__name__)


def _get_service_name():
    for service_name_var in ("DD_SERVICE", "DD_SERVICE_NAME", "DATADOG_SERVICE_NAME"):
        service_name = os.environ.get(service_name_var)
        if service_name is not None:
            return service_name


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
            if hasattr(os, "register_at_fork"):
                os.register_at_fork(after_in_child=self._restart_on_fork)
            else:
                LOG.warning(
                    "Your Python version does not have `os.register_at_fork`. "
                    "You have to start a new Profiler after fork() manually."
                )

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
            self._profiler.stop(flush=False)
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
    service = attr.ib(factory=_get_service_name)
    tags = attr.ib(factory=dict, type=typing.Dict[str, bytes])
    env = attr.ib(factory=lambda: os.environ.get("DD_ENV"))
    version = attr.ib(factory=lambda: os.environ.get("DD_VERSION"))
    tracer = attr.ib(default=ddtrace.tracer)
    api_key = attr.ib(factory=lambda: os.environ.get("DD_API_KEY"), type=Optional[str])
    agentless = attr.ib(factory=lambda: formats.asbool(os.environ.get("DD_PROFILING_AGENTLESS", "False")), type=bool)
    asyncio_loop_policy = attr.ib(factory=DdtraceProfilerEventLoopPolicy, repr=False, eq=False)

    _recorder = attr.ib(init=False, default=None)
    _collectors = attr.ib(init=False, default=None)
    _scheduler = attr.ib(init=False, default=None)

    ENDPOINT_TEMPLATE = "https://intake.profile.{}"

    def _build_default_exporters(self):
        # type: (...) -> List[exporter.Exporter]
        _OUTPUT_PPROF = os.environ.get("DD_PROFILING_OUTPUT_PPROF")
        if _OUTPUT_PPROF:
            return [
                file.PprofFileExporter(_OUTPUT_PPROF),
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
            endpoint_path = "/v1/input"
        else:
            # Agent mode
            # path is relative because it is appended
            # to the agent base path.
            endpoint_path = "profiling/v1/input"

        return [
            http.PprofHTTPExporter(
                service=self.service,
                env=self.env,
                tags=self.tags,
                version=self.version,
                api_key=self.api_key,
                endpoint=endpoint,
                endpoint_path=endpoint_path,
            ),
        ]

    def __attrs_post_init__(self):
        r = self._recorder = recorder.Recorder(
            max_events={
                # Allow to store up to 10 threads for 60 seconds at 100 Hz
                stack_event.StackSampleEvent: 10 * 60 * 100,
                stack_event.StackExceptionSampleEvent: 10 * 60 * 100,
                # (default buffer size / interval) * export interval
                memalloc.MemoryAllocSampleEvent: int(
                    (memalloc.MemoryCollector._DEFAULT_MAX_EVENTS / memalloc.MemoryCollector._DEFAULT_INTERVAL) * 60
                ),
                # Do not limit the heap sample size as the number of events is relative to allocated memory anyway
                memalloc.MemoryHeapSampleEvent: None,
            },
            default_max_events=int(os.environ.get("DD_PROFILING_MAX_EVENTS", recorder.Recorder._DEFAULT_MAX_EVENTS)),
        )

        self._collectors = [
            stack.StackCollector(r, tracer=self.tracer),
            memalloc.MemoryCollector(r),
            threading.ThreadingLockCollector(r, tracer=self.tracer),
        ]

        exporters = self._build_default_exporters()

        if exporters:
            self._scheduler = scheduler.Scheduler(
                recorder=r, exporters=exporters, before_flush=self._collectors_snapshot
            )

        self.set_asyncio_event_loop_policy()

    def set_asyncio_event_loop_policy(self):
        if self.asyncio_loop_policy is not None:
            _asyncio.set_event_loop_policy(self.asyncio_loop_policy)

    def _collectors_snapshot(self):
        for c in self._collectors:
            try:
                snapshot = c.snapshot()
                if snapshot:
                    for events in snapshot:
                        self._recorder.push_events(events)
            except Exception:
                LOG.error("Error while snapshoting collector %r", c, exc_info=True)

    def copy(self):
        return self.__class__(
            service=self.service, env=self.env, version=self.version, tracer=self.tracer, tags=self.tags
        )

    def _start_service(self):  # type: ignore[override]
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

    def _stop_service(  # type: ignore[override]
        self, flush=True  # type: bool
    ):
        # type: (...) -> None
        """Stop the profiler.

        :param flush: Flush a last profile.
        """
        if self._scheduler is not None:
            self._scheduler.stop()
            # Wait for the export to be over: export might need collectors (e.g., for snapshot) so we can't stop
            # collectors before the possibly running flush is finished.
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

        for col in reversed(self._collectors):
            col.join()
