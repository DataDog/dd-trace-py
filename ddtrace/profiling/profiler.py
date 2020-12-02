# -*- encoding: utf-8 -*-
import atexit
import logging
import os
from typing import Optional, List

import ddtrace
from ddtrace.profiling import recorder
from ddtrace.profiling import scheduler
from ddtrace.utils import deprecation
from ddtrace.utils import formats
from ddtrace.vendor import attr
from ddtrace.profiling.collector import memalloc
from ddtrace.profiling.collector import memory
from ddtrace.profiling.collector import stack
from ddtrace.profiling.collector import threading
from ddtrace.profiling import exporter
from ddtrace.profiling.exporter import file
from ddtrace.profiling.exporter import http


LOG = logging.getLogger(__name__)


ENDPOINT_TEMPLATE = "https://intake.profile.{}/v1/input"


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


# This ought to use `enum.Enum`, but since it's not available in Python 2, we just use a dumb class.
@attr.s(repr=False)
class ProfilerStatus(object):
    """A Profiler status."""

    status = attr.ib()

    def __repr__(self):
        return self.status.upper()


ProfilerStatus.STOPPED = ProfilerStatus("stopped")
ProfilerStatus.RUNNING = ProfilerStatus("running")


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
                                 The new profiler object will be stored in the `child` attribute.
        """

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

        :param flush: Wait for the flush of the remaining events before stopping.
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
            default_hostname = "localhost"
            default_port = 8126
            scheme = "http"
            path = ""
        else:
            default_hostname = tracer.writer.api.hostname
            default_port = tracer.writer.api.port
            if tracer.writer.api.https:
                scheme = "https"
                path = ""
            elif tracer.writer.api.uds_path is not None:
                scheme = "unix"
                path = tracer.writer.api.uds_path
            else:
                scheme = "http"
                path = ""

        hostname = os.environ.get("DD_AGENT_HOST", os.environ.get("DATADOG_TRACE_AGENT_HOSTNAME", default_hostname))
        port = int(os.environ.get("DD_TRACE_AGENT_PORT", default_port))

        return os.environ.get("DD_TRACE_AGENT_URL", "%s://%s:%d%s" % (scheme, hostname, port, path))

    # Agentless mode
    legacy = os.environ.get("DD_PROFILING_API_URL")
    if legacy:
        deprecation.deprecation("DD_PROFILING_API_URL", "Use DD_SITE")
        return legacy
    site = os.environ.get("DD_SITE", "datadoghq.com")
    return ENDPOINT_TEMPLATE.format(site)


@attr.s
class _ProfilerInstance(object):
    """A instance of the profiler.

    Each process must manage its own instance.

    """

    # User-supplied values
    url = attr.ib(default=None)
    service = attr.ib(factory=_get_service_name)
    env = attr.ib(factory=lambda: os.environ.get("DD_ENV"))
    version = attr.ib(factory=lambda: os.environ.get("DD_VERSION"))
    tracer = attr.ib(default=ddtrace.tracer)

    _recorder = attr.ib(init=False, default=None)
    _collectors = attr.ib(init=False, default=None)
    _scheduler = attr.ib(init=False, default=None)
    status = attr.ib(init=False, type=ProfilerStatus, default=ProfilerStatus.STOPPED)

    @staticmethod
    def _build_default_exporters(
        tracer,  # type: Optional[ddtrace.Tracer]
        url,  # type: Optional[str]
        service,  # type: Optional[str]
        env,  # type: Optional[str]
        version,  # type: Optional[str]
    ):
        # type: (...) -> List[exporter.Exporter]
        _OUTPUT_PPROF = os.environ.get("DD_PROFILING_OUTPUT_PPROF")
        if _OUTPUT_PPROF:
            return [
                file.PprofFileExporter(_OUTPUT_PPROF),
            ]

        api_key = _get_api_key()

        if api_key is None:
            # Agent mode
            endpoint_path = "/profiling/v1/input"
        else:
            endpoint_path = "/v1/input"

        endpoint = _get_default_url(tracer, api_key) if url is None else url

        return [
            http.PprofHTTPExporter(
                service=service,
                env=env,
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
                # This can generate one event every 0.1s if 100% are taken — though we take 5% by default.
                # = (60 seconds / 0.1 seconds)
                memory.MemorySampleEvent: int(60 / 0.1),
                # (default buffer size / interval) * export interval
                memalloc.MemoryAllocSampleEvent: int(
                    (memalloc.MemoryCollector._DEFAULT_MAX_EVENTS / memalloc.MemoryCollector._DEFAULT_INTERVAL) * 60
                ),
            },
            default_max_events=int(os.environ.get("DD_PROFILING_MAX_EVENTS", recorder.Recorder._DEFAULT_MAX_EVENTS)),
        )

        if formats.asbool(os.environ.get("DD_PROFILING_MEMALLOC", "true")):
            mem_collector = memalloc.MemoryCollector(r)
        else:
            mem_collector = memory.MemoryCollector(r)

        self._collectors = [
            stack.StackCollector(r, tracer=self.tracer),
            mem_collector,
            threading.LockCollector(r, tracer=self.tracer),
        ]

        exporters = self._build_default_exporters(self.tracer, self.url, self.service, self.env, self.version)

        if exporters:
            self._scheduler = scheduler.Scheduler(recorder=r, exporters=exporters)

    def copy(self):
        return self.__class__(service=self.service, env=self.env, version=self.version, tracer=self.tracer)

    def start(self):
        """Start the profiler."""
        for col in self._collectors:
            try:
                col.start()
            except RuntimeError:
                # `tracemalloc` is unavailable?
                pass

        if self._scheduler is not None:
            self._scheduler.start()

        self.status = ProfilerStatus.RUNNING

    def stop(self, flush=True):
        """Stop the profiler.

        :param flush: Wait for the flush of the remaining events before stopping.
        """
        if self._scheduler:
            self._scheduler.stop()

        for col in reversed(self._collectors):
            col.stop()

        for col in reversed(self._collectors):
            col.join()

        if self._scheduler and flush:
            self._scheduler.join()

        self.status = ProfilerStatus.STOPPED

        # Python 2 does not have unregister
        if hasattr(atexit, "unregister"):
            # You can unregister a method that was not registered, so no need to do any other check
            atexit.unregister(self.stop)
