# -*- encoding: utf-8 -*-
import atexit
import logging
import os
import sys
from typing import AnyStr
from typing import Dict
from typing import List
from typing import Optional
import warnings

import ddtrace
from ddtrace.internal import agent
from ddtrace.internal import service
from ddtrace.internal import uwsgi
from ddtrace.profiling import collector
from ddtrace.profiling import exporter
from ddtrace.profiling import recorder
from ddtrace.profiling import scheduler
from ddtrace.profiling.collector import memalloc
from ddtrace.profiling.collector import stack
from ddtrace.profiling.collector import threading
from ddtrace.profiling.exporter import file
from ddtrace.profiling.exporter import http
from ddtrace.utils import deprecation
from ddtrace.vendor import attr


LOG = logging.getLogger(__name__)


def _get_api_key():
    legacy = os.environ.get("DD_PROFILING_API_KEY")
    if legacy:
        deprecation.deprecation("DD_PROFILING_API_KEY", "Use DD_API_KEY")
        return legacy
    return os.environ.get("DD_API_KEY")


def _get_service_name():
    for service_name_var in ("DD_SERVICE", "DD_SERVICE_NAME", "DATADOG_SERVICE_NAME"):
        service_name = os.environ.get(service_name_var)
        if service_name is not None:
            return service_name


def gevent_patch_all(event):
    if "ddtrace.profiling.auto" in sys.modules:
        warnings.warn(
            "Starting the profiler before using gevent monkey patching is not supported "
            "and is likely to break the application. Use DD_GEVENT_PATCH_ALL=true to avoid this.",
            RuntimeWarning,
        )


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
        self._profiler.stop(flush)

    def _restart_on_fork(self):
        # Be sure to stop the parent first, since it might have to e.g. unpatch functions
        # Do not flush data as we don't want to have multiple copies of the parent profile exported.
        self.stop(flush=False)
        self._profiler = self._profiler.copy()
        self._profiler.start()

    @property
    def status(self):
        return self._profiler.status

    @property
    def service(self):
        return self._profiler.service

    @property
    def env(self):
        return self._profiler.env

    @property
    def version(self):
        return self._profiler.version

    @property
    def tracer(self):
        return self._profiler.tracer

    @property
    def url(self):
        return self._profiler.url

    @property
    def tags(self):
        return self._profiler.tags


ENDPOINT_TEMPLATE = "https://intake.profile.{}"


def _get_default_url(
    tracer,  # type: Optional[ddtrace.Tracer]
    api_key,  # type: str
):
    # type: (...) -> str
    """Get default profiler exporter URL.

    If an API key is not specified, the URL will default to the agent location configured via the environment variables.

    If an API key is specified, the profiler goes into agentless mode and uses `DD_SITE` to upload directly to Datadog
    backend.

    :param api_key: The API key provided by the user.
    :param tracer: The tracer object to use to find default URL.
    """
    # Default URL changes if an API_KEY is provided in the env
    if api_key is None:
        if tracer is None:
            return agent.get_trace_url()
        else:
            # Only use the tracer writer URL if it has been modified by the user.
            if tracer.writer.agent_url != agent.get_trace_url() and tracer.writer.agent_url != agent.DEFAULT_TRACE_URL:
                return tracer.writer.agent_url
            else:
                return agent.get_trace_url()
    # Agentless mode
    legacy = os.environ.get("DD_PROFILING_API_URL")
    if legacy:
        deprecation.deprecation("DD_PROFILING_API_URL", "Use DD_SITE")
        return legacy
    site = os.environ.get("DD_SITE", "datadoghq.com")
    return ENDPOINT_TEMPLATE.format(site)


@attr.s
class _ProfilerInstance(service.Service):
    """A instance of the profiler.

    Each process must manage its own instance.

    """

    # User-supplied values
    url = attr.ib(default=None)
    service = attr.ib(factory=_get_service_name)
    tags = attr.ib(factory=dict)
    env = attr.ib(factory=lambda: os.environ.get("DD_ENV"))
    version = attr.ib(factory=lambda: os.environ.get("DD_VERSION"))
    tracer = attr.ib(default=ddtrace.tracer)

    _recorder = attr.ib(init=False, default=None)
    _collectors = attr.ib(init=False, default=None)
    _scheduler = attr.ib(init=False, default=None)

    @staticmethod
    def _build_default_exporters(
        tracer,  # type: Optional[ddtrace.Tracer]
        url,  # type: Optional[str]
        tags,  # type: Dict[str, AnyStr]
        service,  # type: Optional[str]
        env,  # type: Optional[str]
        version,  # type: Optional[str]
    ):
        # type: (...) -> List[exporter.Exporter]
        _OUTPUT_PPROF = os.environ.get("DD_PROFILING_OUTPUT_PPROF")
        if _OUTPUT_PPROF:
            return [
                file.PprofFileExporter(_OUTPUT_PPROF),  # type: ignore[call-arg]
            ]

        api_key = _get_api_key()

        if api_key is None:
            # Agent mode
            endpoint_path = "/profiling/v1/input"
        else:
            endpoint_path = "/v1/input"

        endpoint = _get_default_url(tracer, api_key) if url is None else url

        return [
            http.PprofHTTPExporter(  # type: ignore[call-arg]
                service=service,
                env=env,
                tags=tags,
                version=version,
                api_key=api_key,
                endpoint=endpoint,
                endpoint_path=endpoint_path,
            ),
        ]

    def __attrs_post_init__(self):
        r = self._recorder = recorder.Recorder(
            max_events={
                # Allow to store up to 10 threads for 60 seconds at 100 Hz
                stack.StackSampleEvent: 10 * 60 * 100,
                stack.StackExceptionSampleEvent: 10 * 60 * 100,
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
            threading.LockCollector(r, tracer=self.tracer),
        ]

        exporters = self._build_default_exporters(
            self.tracer, self.url, self.tags, self.service, self.env, self.version
        )

        if exporters:
            self._scheduler = scheduler.Scheduler(
                recorder=r, exporters=exporters, before_flush=self._collectors_snapshot
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

    def copy(self):
        return self.__class__(
            service=self.service, env=self.env, version=self.version, tracer=self.tracer, tags=self.tags
        )

    def _start(self):
        # type: () -> None
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

    def stop(self, flush=True):
        """Stop the profiler.

        :param flush: Flush a last profile.
        """
        if self.status != service.ServiceStatus.RUNNING:
            return

        if self._scheduler:
            self._scheduler.stop()
            # Wait for the export to be over: export might need collectors (e.g., for snapshot) so we can't stop
            # collectors before the possibly running flush is finished.
            self._scheduler.join()
            if flush:
                # Do not stop the collectors before flushing, they might be needed (snapshot)
                self._scheduler.flush()

        for col in reversed(self._collectors):
            col.stop()

        for col in reversed(self._collectors):
            col.join()

        # Python 2 does not have unregister
        if hasattr(atexit, "unregister"):
            # You can unregister a method that was not registered, so no need to do any other check
            atexit.unregister(self.stop)

        super(_ProfilerInstance, self).stop()
