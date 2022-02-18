import functools
import json
import logging
import os
from os import environ
from os import getpid
import sys
from threading import RLock
from typing import Any
from typing import Callable
from typing import Dict
from typing import List
from typing import Optional
from typing import Set
from typing import TypeVar
from typing import Union

from ddtrace import config
from ddtrace.filters import TraceFilter
from ddtrace.internal.utils.deprecation import deprecation
from ddtrace.vendor import debtcollector

from . import _hooks
from ._monkey import patch
from .constants import AUTO_KEEP
from .constants import AUTO_REJECT
from .constants import ENV_KEY
from .constants import FILTERS_KEY
from .constants import HOSTNAME_KEY
from .constants import PID
from .constants import SAMPLE_RATE_METRIC_KEY
from .constants import VERSION_KEY
from .context import Context
from .internal import agent
from .internal import atexit
from .internal import compat
from .internal import debug
from .internal import forksafe
from .internal import hostname
from .internal import service
from .internal.dogstatsd import get_dogstatsd_client
from .internal.logger import get_logger
from .internal.logger import hasHandlers
from .internal.processor import SpanProcessor
from .internal.processor.trace import SpanAggregator
from .internal.processor.trace import TraceProcessor
from .internal.processor.trace import TraceSamplingProcessor
from .internal.processor.trace import TraceTagsProcessor
from .internal.processor.trace import TraceTopLevelSpanProcessor
from .internal.runtime import get_runtime_id
from .internal.utils.deprecation import deprecated
from .internal.utils.formats import asbool
from .internal.utils.formats import get_env
from .internal.writer import AgentWriter
from .internal.writer import LogWriter
from .internal.writer import TraceWriter
from .provider import DefaultContextProvider
from .sampler import BasePrioritySampler
from .sampler import BaseSampler
from .sampler import DatadogSampler
from .sampler import RateByServiceSampler
from .sampler import RateSampler
from .span import Span
from .vendor.debtcollector import removals


log = get_logger(__name__)

debug_mode = asbool(get_env("trace", "debug", default=False))
call_basic_config = asbool(os.environ.get("DD_CALL_BASIC_CONFIG", "true"))

DD_LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] [%(filename)s:%(lineno)d] {}- %(message)s".format(
    "[dd.service=%(dd.service)s dd.env=%(dd.env)s dd.version=%(dd.version)s"
    " dd.trace_id=%(dd.trace_id)s dd.span_id=%(dd.span_id)s] "
)
if debug_mode and not hasHandlers(log) and call_basic_config:
    if config.logs_injection:
        # We need to ensure logging is patched in case the tracer logs during initialization
        patch(logging=True)
        logging.basicConfig(level=logging.DEBUG, format=DD_LOG_FORMAT)
    else:
        logging.basicConfig(level=logging.DEBUG)


_INTERNAL_APPLICATION_SPAN_TYPES = {"custom", "template", "web", "worker"}


AnyCallable = TypeVar("AnyCallable", bound=Callable)


class Tracer(object):
    """
    Tracer is used to create, sample and submit spans that measure the
    execution time of sections of code.

    If you're running an application that will serve a single trace per thread,
    you can use the global tracer instance::

        from ddtrace import tracer
        trace = tracer.trace('app.request', 'web-server').finish()
    """

    SHUTDOWN_TIMEOUT = 5

    def __init__(
        self,
        url=None,  # type: Optional[str]
        dogstatsd_url=None,  # type: Optional[str]
    ):
        # type: (...) -> None
        """
        Create a new ``Tracer`` instance. A global tracer is already initialized
        for common usage, so there is no need to initialize your own ``Tracer``.

        :param url: The Datadog agent URL.
        :param dogstatsd_url: The DogStatsD URL.
        """
        self.log = log
        self._filters = []  # type: List[TraceFilter]

        # globally set tags
        self.tags = config.tags.copy()

        # a buffer for service info so we don't perpetually send the same things
        self._services = set()  # type: Set[str]

        # Runtime id used for associating data collected during runtime to
        # traces
        self._pid = getpid()

        self.enabled = asbool(get_env("trace", "enabled", default=True))
        self.context_provider = DefaultContextProvider()
        self.sampler = DatadogSampler()  # type: BaseSampler
        self.priority_sampler = RateByServiceSampler()  # type: Optional[BasePrioritySampler]
        self._dogstatsd_url = agent.get_stats_url() if dogstatsd_url is None else dogstatsd_url

        if self._use_log_writer() and url is None:
            writer = LogWriter()  # type: TraceWriter
        else:
            url = url or agent.get_trace_url()
            agent.verify_url(url)

            writer = AgentWriter(
                agent_url=url,
                sampler=self.sampler,
                priority_sampler=self.priority_sampler,
                dogstatsd=get_dogstatsd_client(self._dogstatsd_url),
                report_metrics=config.health_metrics_enabled,
                sync_mode=self._use_sync_mode(),
            )
        self._writer = writer  # type: TraceWriter

        # DD_TRACER_... should be deprecated after version 1.0.0 is released
        pfe_default_value = False
        pfms_default_value = 500
        if "DD_TRACER_PARTIAL_FLUSH_ENABLED" in os.environ or "DD_TRACER_PARTIAL_FLUSH_MIN_SPANS" in os.environ:
            deprecation("DD_TRACER_... use DD_TRACE_... instead", version="1.0.0")
            pfe_default_value = asbool(get_env("tracer", "partial_flush_enabled", default=pfe_default_value))
            pfms_default_value = int(
                get_env("tracer", "partial_flush_min_spans", default=pfms_default_value)  # type: ignore[arg-type]
            )
        self._partial_flush_enabled = asbool(get_env("trace", "partial_flush_enabled", default=pfe_default_value))
        self._partial_flush_min_spans = int(
            get_env("trace", "partial_flush_min_spans", default=pfms_default_value)  # type: ignore[arg-type]
        )

        self._initialize_span_processors()
        self._hooks = _hooks.Hooks()
        atexit.register(self._atexit)
        forksafe.register(self._child_after_fork)

        self._shutdown_lock = RLock()

        self._new_process = False

    def _atexit(self):
        # type: () -> None
        key = "ctrl-break" if os.name == "nt" else "ctrl-c"
        log.debug(
            "Waiting %d seconds for tracer to finish. Hit %s to quit.",
            self.SHUTDOWN_TIMEOUT,
            key,
        )
        self.shutdown(timeout=self.SHUTDOWN_TIMEOUT)

    def on_start_span(self, func):
        # type: (Callable) -> Callable
        """Register a function to execute when a span start.

        Can be used as a decorator.

        :param func: The function to call when starting a span.
                     The started span will be passed as argument.
        """
        self._hooks.register(self.__class__.start_span, func)
        return func

    def deregister_on_start_span(self, func):
        # type: (Callable) -> Callable
        """Unregister a function registered to execute when a span starts.

        Can be used as a decorator.

        :param func: The function to stop calling when starting a span.
        """

        self._hooks.deregister(self.__class__.start_span, func)
        return func

    @property
    def debug_logging(self):
        return self.log.isEnabledFor(logging.DEBUG)

    @debug_logging.setter  # type: ignore[misc]
    @deprecated(message="Use logging.setLevel instead", version="1.0.0")
    def debug_logging(self, value):
        # type: (bool) -> None
        self.log.setLevel(logging.DEBUG if value else logging.WARN)

    @deprecated("Use .tracer, not .tracer()", "1.0.0")
    def __call__(self):
        return self

    @deprecated("This method will be removed altogether", "1.0.0")
    def global_excepthook(self, tp, value, traceback):
        """The global tracer except hook."""

    @deprecated(
        "Call context has been superseded by trace context. Please use current_trace_context() instead.", "1.0.0"
    )
    def get_call_context(self, *args, **kwargs):
        # type: (...) -> Context
        """
        Return the current active ``Context`` for this traced execution. This method is
        automatically called in the ``tracer.trace()``, but it can be used in the application
        code during manual instrumentation like::

            from ddtrace import tracer

            async def web_handler(request):
                context = tracer.get_call_context()
                # use the context if needed
                # ...

        This method makes use of a ``ContextProvider`` that is automatically set during the tracer
        initialization, or while using a library instrumentation.
        """
        ctx = self.current_trace_context(*args, **kwargs)
        if ctx is None:
            ctx = Context()
        return ctx

    def current_trace_context(self, *args, **kwargs):
        # type: (...) -> Optional[Context]
        """Return the context for the current trace.

        If there is no active trace then None is returned.
        """
        active = self.context_provider.active()
        if isinstance(active, Context):
            return active
        elif isinstance(active, Span):
            return active.context
        return None

    def get_log_correlation_context(self):
        # type: () -> Dict[str, str]
        """Retrieves the data used to correlate a log with the current active trace.
        Generates a dictionary for custom logging instrumentation including the trace id and
        span id of the current active span, as well as the configured service, version, and environment names.
        If there is no active span, a dictionary with an empty string for each value will be returned.
        """
        span = None
        if self.enabled:
            span = self.current_span()

        if span and span.service:
            service = span.service
        else:
            service = config.service

        return {
            "trace_id": str(span.trace_id) if span else "0",
            "span_id": str(span.span_id) if span else "0",
            "service": service or "",
            "version": config.version or "",
            "env": config.env or "",
        }

    # TODO: deprecate this method and make sure users create a new tracer if they need different parameters
    def configure(
        self,
        enabled=None,  # type: Optional[bool]
        hostname=None,  # type: Optional[str]
        port=None,  # type: Optional[int]
        uds_path=None,  # type: Optional[str]
        https=None,  # type: Optional[bool]
        sampler=None,  # type: Optional[BaseSampler]
        context_provider=None,  # type: Optional[DefaultContextProvider]
        wrap_executor=None,  # type: Optional[Callable]
        priority_sampling=None,  # type: Optional[bool]
        settings=None,  # type: Optional[Dict[str, Any]]
        dogstatsd_url=None,  # type: Optional[str]
        writer=None,  # type: Optional[TraceWriter]
        partial_flush_enabled=None,  # type: Optional[bool]
        partial_flush_min_spans=None,  # type: Optional[int]
        api_version=None,  # type: Optional[str]
    ):
        # type: (...) -> None
        """
        Configure an existing Tracer the easy way.
        Allow to configure or reconfigure a Tracer instance.

        :param bool enabled: If True, finished traces will be submitted to the API.
            Otherwise they'll be dropped.
        :param str hostname: Hostname running the Trace Agent
        :param int port: Port of the Trace Agent
        :param str uds_path: The Unix Domain Socket path of the agent.
        :param bool https: Whether to use HTTPS or HTTP.
        :param object sampler: A custom Sampler instance, locally deciding to totally drop the trace or not.
        :param object context_provider: The ``ContextProvider`` that will be used to retrieve
            automatically the current call context. This is an advanced option that usually
            doesn't need to be changed from the default value
        :param object wrap_executor: callable that is used when a function is decorated with
            ``Tracer.wrap()``. This is an advanced option that usually doesn't need to be changed
            from the default value
        :param priority_sampling: enable priority sampling, this is required for
            complete distributed tracing support. Enabled by default.
        :param str dogstatsd_url: URL for UDP or Unix socket connection to DogStatsD
        """
        if enabled is not None:
            self.enabled = enabled

        if settings is not None:
            filters = settings.get(FILTERS_KEY)
            if filters is not None:
                self._filters = filters

        if partial_flush_enabled is not None:
            self._partial_flush_enabled = partial_flush_enabled

        if partial_flush_min_spans is not None:
            self._partial_flush_min_spans = partial_flush_min_spans

        # If priority sampling is not set or is True and no priority sampler is set yet
        if priority_sampling in (None, True) and not self.priority_sampler:
            self.priority_sampler = RateByServiceSampler()
        # Explicitly disable priority sampling
        elif priority_sampling is False:
            self.priority_sampler = None

        if sampler is not None:
            self.sampler = sampler

        self._dogstatsd_url = dogstatsd_url or self._dogstatsd_url

        if any(x is not None for x in [hostname, port, uds_path, https]):
            # If any of the parts of the URL have updated, merge them with
            # the previous writer values.
            if isinstance(self._writer, AgentWriter):
                prev_url_parsed = compat.parse.urlparse(self._writer.agent_url)
            else:
                prev_url_parsed = compat.parse.urlparse("")

            if uds_path is not None:
                if hostname is None and prev_url_parsed.scheme == "unix":
                    hostname = prev_url_parsed.hostname
                url = "unix://%s%s" % (hostname or "", uds_path)
            else:
                if https is None:
                    https = prev_url_parsed.scheme == "https"
                if hostname is None:
                    hostname = prev_url_parsed.hostname or ""
                if port is None:
                    port = prev_url_parsed.port
                scheme = "https" if https else "http"
                url = "%s://%s:%s" % (scheme, hostname, port)
        elif isinstance(self._writer, AgentWriter):
            # Reuse the URL from the previous writer if there was one.
            url = self._writer.agent_url
        else:
            # No URL parts have updated and there's no previous writer to
            # get the URL from.
            url = None

        try:
            self._writer.stop()
        except service.ServiceStatusError:
            # It's possible the writer never got started in the first place :(
            pass

        if writer is not None:
            self._writer = writer
        elif url:
            # Verify the URL and create a new AgentWriter with it.
            agent.verify_url(url)
            self._writer = AgentWriter(
                url,
                sampler=self.sampler,
                priority_sampler=self.priority_sampler,
                dogstatsd=get_dogstatsd_client(self._dogstatsd_url),
                report_metrics=config.health_metrics_enabled,
                sync_mode=self._use_sync_mode(),
                api_version=api_version,
            )
        elif writer is None and isinstance(self._writer, LogWriter):
            # No need to do anything for the LogWriter.
            pass
        if isinstance(self._writer, AgentWriter):
            self._writer.dogstatsd = get_dogstatsd_client(self._dogstatsd_url)  # type: ignore[has-type]
        self._initialize_span_processors()

        if context_provider is not None:
            self.context_provider = context_provider

        if wrap_executor is not None:
            self._wrap_executor = wrap_executor

        if debug_mode or asbool(environ.get("DD_TRACE_STARTUP_LOGS", False)):
            try:
                info = debug.collect(self)
            except Exception as e:
                msg = "Failed to collect start-up logs: %s" % e
                self._log_compat(logging.WARNING, "- DATADOG TRACER DIAGNOSTIC - %s" % msg)
            else:
                if self.log.isEnabledFor(logging.INFO):
                    msg = "- DATADOG TRACER CONFIGURATION - %s" % json.dumps(info)
                    self._log_compat(logging.INFO, msg)

                # Always log errors since we're either in debug_mode or start up logs
                # are enabled.
                agent_error = info.get("agent_error")
                if agent_error:
                    msg = "- DATADOG TRACER DIAGNOSTIC - %s" % agent_error
                    self._log_compat(logging.WARNING, msg)

    def _child_after_fork(self):
        self._pid = getpid()

        # Assume that the services of the child are not necessarily a subset of those
        # of the parent.
        self._services = set()

        # Re-create the background writer thread
        self._writer = self._writer.recreate()
        self._initialize_span_processors()

        self._new_process = True

    def _start_span(
        self,
        name,  # type: str
        child_of=None,  # type: Optional[Union[Span, Context]]
        service=None,  # type: Optional[str]
        resource=None,  # type: Optional[str]
        span_type=None,  # type: Optional[str]
        activate=False,  # type: bool
    ):
        # type: (...) -> Span
        """Return a span that represents an operation called ``name``.

        Note that the :meth:`.trace` method will almost always be preferred
        over this method as it provides automatic span parenting. This method
        should only be used if manual parenting is desired.

        :param str name: the name of the operation being traced.
        :param object child_of: a ``Span`` or a ``Context`` instance representing the parent for this span.
        :param str service: the name of the service being traced.
        :param str resource: an optional name of the resource being tracked.
        :param str span_type: an optional operation type.
        :param activate: activate the span once it is created.

        To start a new root span::

            span = tracer.start_span("web.request")

        To create a child for a root span::

            root_span = tracer.start_span("web.request")
            span = tracer.start_span("web.decoder", child_of=root_span)

        Spans from ``start_span`` are not activated by default::

            with tracer.start_span("parent") as parent:
                assert tracer.current_span() is None
                with tracer.start_span("child", child_of=parent):
                    assert tracer.current_span() is None

            new_parent = tracer.start_span("new_parent", activate=True)
            assert tracer.current_span() is new_parent

        Note: be sure to finish all spans to avoid memory leaks and incorrect
        parenting of spans.
        """
        if self._new_process:
            self._new_process = False

            # The spans remaining in the context can not and will not be
            # finished in this new process. So to avoid memory leaks the
            # strong span reference (which will never be finished) is replaced
            # with a context representing the span.
            if isinstance(child_of, Span):
                new_ctx = Context(
                    sampling_priority=child_of.context.sampling_priority,
                    span_id=child_of.span_id,
                    trace_id=child_of.trace_id,
                )

                # If the child_of span was active then activate the new context
                # containing it so that the strong span referenced is removed
                # from the execution.
                if self.context_provider.active() is child_of:
                    self.context_provider.activate(new_ctx)
                child_of = new_ctx

        parent = None  # type: Optional[Span]
        if child_of is not None:
            if isinstance(child_of, Context):
                context = child_of
            else:
                context = child_of.context
                parent = child_of
        else:
            context = Context()

        if parent:
            trace_id = parent.trace_id  # type: Optional[int]
            parent_id = parent.span_id  # type: Optional[int]
        else:
            trace_id = context.trace_id
            parent_id = context.span_id

        # The following precedence is used for a new span's service:
        # 1. Explicitly provided service name
        #     a. User provided or integration provided service name
        # 2. Parent's service name (if defined)
        # 3. Globally configured service name
        #     a. `config.service`/`DD_SERVICE`/`DD_TAGS`
        if service is None:
            if parent:
                service = parent.service
            else:
                service = config.service

        mapped_service = config.service_mapping.get(service, service)

        if trace_id:
            # child_of a non-empty context, so either a local child span or from a remote context
            span = Span(
                self,
                name,
                context=context,
                trace_id=trace_id,
                parent_id=parent_id,
                service=mapped_service,
                resource=resource,
                span_type=span_type,
                on_finish=[self._on_span_finish],
            )

            # Extra attributes when from a local parent
            if parent:
                span.sampled = parent.sampled
                span._parent = parent
                span._local_root = parent._local_root

            if span._local_root is None:
                span._local_root = span
        else:
            # this is the root span of a new trace
            span = Span(
                self,
                name,
                context=context,
                service=mapped_service,
                resource=resource,
                span_type=span_type,
                on_finish=[self._on_span_finish],
            )
            span._local_root = span
            if config.report_hostname:
                span._set_str_tag(HOSTNAME_KEY, hostname.get_hostname())
            span.sampled = self.sampler.sample(span)
            # Old behavior
            # DEV: The new sampler sets metrics and priority sampling on the span for us
            if not isinstance(self.sampler, DatadogSampler):
                if span.sampled:
                    # When doing client sampling in the client, keep the sample rate so that we can
                    # scale up statistics in the next steps of the pipeline.
                    if isinstance(self.sampler, RateSampler):
                        span.set_metric(SAMPLE_RATE_METRIC_KEY, self.sampler.sample_rate)

                    if self.priority_sampler:
                        # At this stage, it's important to have the service set. If unset,
                        # priority sampler will use the default sampling rate, which might
                        # lead to oversampling (that is, dropping too many traces).
                        if self.priority_sampler.sample(span):
                            context.sampling_priority = AUTO_KEEP
                        else:
                            context.sampling_priority = AUTO_REJECT
                else:
                    if self.priority_sampler:
                        # If dropped by the local sampler, distributed instrumentation can drop it too.
                        context.sampling_priority = AUTO_REJECT
            else:
                # We must always mark the span as sampled so it is forwarded to the agent
                span.sampled = True

        if not span._parent:
            span._set_str_tag("runtime-id", get_runtime_id())
            span.metrics[PID] = self._pid

        # Apply default global tags.
        if self.tags:
            span.set_tags(self.tags)

        if config.env:
            span._set_str_tag(ENV_KEY, config.env)

        # Only set the version tag on internal spans.
        if config.version:
            root_span = self.current_root_span()
            # if: 1. the span is the root span and the span's service matches the global config; or
            #     2. the span is not the root, but the root span's service matches the span's service
            #        and the root span has a version tag
            # then the span belongs to the user application and so set the version tag
            if (root_span is None and service == config.service) or (
                root_span and root_span.service == service and root_span.get_tag(VERSION_KEY) is not None
            ):
                span._set_str_tag(VERSION_KEY, config.version)

        if activate:
            self.context_provider.activate(span)

        # update set of services handled by tracer
        if service and service not in self._services and self._is_span_internal(span):
            self._services.add(service)

        # Only call span processors if the tracer is enabled
        if self.enabled:
            for p in self._span_processors:
                p.on_span_start(span)

        self._hooks.emit(self.__class__.start_span, span)
        return span

    start_span = _start_span

    def _on_span_finish(self, span):
        # type: (Span) -> None
        active = self.current_span()
        # Debug check: if the finishing span has a parent and its parent
        # is not the next active span then this is an error in synchronous tracing.
        if span._parent is not None and active is not span._parent:
            self.log.debug(
                "span %r closing after its parent %r, this is an error when not using async", span, span._parent
            )

        # Only call span processors if the tracer is enabled
        if self.enabled:
            for p in self._span_processors:
                p.on_span_finish(span)

        if self.log.isEnabledFor(logging.DEBUG):
            self.log.debug("finishing span %s (enabled:%s)", span._pprint(), self.enabled)

    def _initialize_span_processors(self, appsec_enabled=asbool(get_env("appsec", "enabled", default=False))):
        # type: (Optional[bool]) -> None
        trace_processors = []  # type: List[TraceProcessor]
        trace_processors += [TraceTagsProcessor()]
        trace_processors += [TraceSamplingProcessor()]
        trace_processors += [TraceTopLevelSpanProcessor()]
        trace_processors += self._filters

        self._span_processors = []  # type: List[SpanProcessor]

        if appsec_enabled:
            try:
                from .appsec.processor import AppSecSpanProcessor

                appsec_span_processor = AppSecSpanProcessor()
                self._span_processors.append(appsec_span_processor)
            except Exception as e:
                # DDAS-001-01
                log.error(
                    "[DDAS-001-01] "
                    "AppSec could not start because of an unexpected error. No security activities will be collected. "
                    "Please contact support at https://docs.datadoghq.com/help/ for help. Error details: \n%s",
                    repr(e),
                )
                if config._raise:
                    raise

        self._span_processors.append(
            SpanAggregator(
                partial_flush_enabled=self._partial_flush_enabled,
                partial_flush_min_spans=self._partial_flush_min_spans,
                trace_processors=trace_processors,
                writer=self._writer,
            )
        )

    def _log_compat(self, level, msg):
        """Logs a message for the given level.

        Python 2 will not submit logs to stderr if no handler is configured.

        Instead, something like this will be printed to stderr:
            No handlers could be found for logger "ddtrace.tracer"

        Since the global tracer is configured on import and it is recommended
        to import the tracer as early as possible, it will likely be the case
        that there are no handlers installed yet.
        """
        if compat.PY2 and not hasHandlers(self.log):
            sys.stderr.write("%s\n" % msg)
        else:
            self.log.log(level, msg)

    def _trace(self, name, service=None, resource=None, span_type=None):
        # type: (str, Optional[str], Optional[str], Optional[str]) -> Span
        """Activate and return a new span that inherits from the current active span.

        :param str name: the name of the operation being traced
        :param str service: the name of the service being traced. If not set,
                            it will inherit the service from its parent.
        :param str resource: an optional name of the resource being tracked.
        :param str span_type: an optional operation type.

        The returned span *must* be ``finish``'d or it will remain in memory
        indefinitely::

            >>> span = tracer.trace("web.request")
                try:
                    # do something
                finally:
                    span.finish()

            >>> with tracer.trace("web.request") as span:
                    # do something

        Example of the automatic parenting::

            parent = tracer.trace("parent")     # has no parent span
            assert tracer.current_span() is parent

            child  = tracer.trace("child")
            assert child.parent_id == parent.span_id
            assert tracer.current_span() is child
            child.finish()

            # parent is now the active span again
            assert tracer.current_span() is parent
            parent.finish()

            assert tracer.current_span() is None

            parent2 = tracer.trace("parent2")
            assert parent2.parent_id is None
            parent2.finish()
        """
        return self.start_span(
            name,
            child_of=self.context_provider.active(),
            service=service,
            resource=resource,
            span_type=span_type,
            activate=True,
        )

    trace = _trace

    def current_root_span(self):
        # type: () -> Optional[Span]
        """Returns the root span of the current execution.

        This is useful for attaching information related to the trace as a
        whole without needing to add to child spans.

        For example::

            # get the root span
            root_span = tracer.current_root_span()
            # set the host just once on the root span
            if root_span:
                root_span.set_tag('host', '127.0.0.1')
        """
        span = self.current_span()
        if span is None:
            return None
        return span._local_root

    def current_span(self):
        # type: () -> Optional[Span]
        """Return the active span in the current execution context.

        Note that there may be an active span represented by a context object
        (like from a distributed trace) which will not be returned by this
        method.
        """
        active = self.context_provider.active()
        return active if isinstance(active, Span) else None

    def write(self, spans):
        # type: (Optional[List[Span]]) -> None
        """
        Send the trace to the writer to enqueue the spans list in the agent
        sending queue.
        """
        if not spans:
            return  # nothing to do

        if self.log.isEnabledFor(logging.DEBUG):
            self.log.debug("writing %s spans (enabled:%s)", len(spans), self.enabled)
            for span in spans:
                self.log.debug("\n%s", span._pprint())

        if not self.enabled:
            return

        if spans is not None:
            self._writer.write(spans=spans)

    @removals.removed_property(message="Use Tracer.flush instead to flush buffered traces to agent", version="1.0.0")
    def writer(self):
        return self._writer

    @property
    def agent_trace_url(self):
        # type: () -> Optional[str]
        """Trace agent url"""
        if isinstance(self._writer, AgentWriter):
            return self._writer.agent_url

        return None

    def flush(self):
        """Flush the buffer of the trace writer. This does nothing if an unbuffered trace writer is used."""
        self._writer.flush_queue()

    @deprecated(message="Manually setting service info is no longer necessary", version="1.0.0")
    def set_service_info(self, *args, **kwargs):
        """Set the information about the given service."""
        return

    def wrap(
        self,
        name=None,  # type: Optional[str]
        service=None,  # type: Optional[str]
        resource=None,  # type: Optional[str]
        span_type=None,  # type: Optional[str]
    ):
        # type: (...) -> Callable[[AnyCallable], AnyCallable]
        """
        A decorator used to trace an entire function. If the traced function
        is a coroutine, it traces the coroutine execution when is awaited.
        If a ``wrap_executor`` callable has been provided in the ``Tracer.configure()``
        method, it will be called instead of the default one when the function
        decorator is invoked.

        :param str name: the name of the operation being traced. If not set,
                         defaults to the fully qualified function name.
        :param str service: the name of the service being traced. If not set,
                            it will inherit the service from it's parent.
        :param str resource: an optional name of the resource being tracked.
        :param str span_type: an optional operation type.

        >>> @tracer.wrap('my.wrapped.function', service='my.service')
            def run():
                return 'run'

        >>> # name will default to 'execute' if unset
            @tracer.wrap()
            def execute():
                return 'executed'

        >>> # or use it in asyncio coroutines
            @tracer.wrap()
            async def coroutine():
                return 'executed'

        >>> @tracer.wrap()
            @asyncio.coroutine
            def coroutine():
                return 'executed'

        You can access the current span using `tracer.current_span()` to set
        tags:

        >>> @tracer.wrap()
            def execute():
                span = tracer.current_span()
                span.set_tag('a', 'b')
        """

        def wrap_decorator(f):
            # type: (AnyCallable) -> AnyCallable
            # FIXME[matt] include the class name for methods.
            span_name = name if name else "%s.%s" % (f.__module__, f.__name__)

            # detect if the the given function is a coroutine to use the
            # right decorator; this initial check ensures that the
            # evaluation is done only once for each @tracer.wrap
            if compat.iscoroutinefunction(f):
                # call the async factory that creates a tracing decorator capable
                # to await the coroutine execution before finishing the span. This
                # code is used for compatibility reasons to prevent Syntax errors
                # in Python 2
                func_wrapper = compat.make_async_decorator(
                    self,
                    f,
                    span_name,
                    service=service,
                    resource=resource,
                    span_type=span_type,
                )
            else:

                @functools.wraps(f)
                def func_wrapper(*args, **kwargs):
                    # if a wrap executor has been configured, it is used instead
                    # of the default tracing function
                    if getattr(self, "_wrap_executor", None):
                        return self._wrap_executor(
                            self,
                            f,
                            args,
                            kwargs,
                            span_name,
                            service=service,
                            resource=resource,
                            span_type=span_type,
                        )

                    # otherwise fallback to a default tracing
                    with self.trace(span_name, service=service, resource=resource, span_type=span_type):
                        return f(*args, **kwargs)

            return func_wrapper

        return wrap_decorator

    def set_tags(self, tags):
        # type: (Dict[str, str]) -> None
        """Set some tags at the tracer level.
        This will append those tags to each span created by the tracer.

        :param dict tags: dict of tags to set at tracer level
        """
        self.tags.update(tags)

    def _restore_from_shutdown(self):
        with self._shutdown_lock:
            if self.start_span is self._start_span:
                # Already restored
                return

            atexit.register(self._atexit)
            forksafe.register(self._child_after_fork)

            self.start_span = self._start_span
            self.trace = self._trace

            debtcollector.deprecate(
                "Tracing with a tracer that has been shut down is being deprecated. "
                "A new tracer should be created for generating new traces",
                version="1.0.0",
            )

    def _shutdown_start_span(
        self,
        name,  # type: str
        child_of=None,  # type: Optional[Union[Span, Context]]
        service=None,  # type: Optional[str]
        resource=None,  # type: Optional[str]
        span_type=None,  # type: Optional[str]
        activate=False,  # type: bool
    ):
        # type: (...) -> Span
        self._restore_from_shutdown()

        return self.start_span(name, child_of, service, resource, span_type, activate)

    def _shutdown_trace(self, name, service=None, resource=None, span_type=None):
        # type: (str, Optional[str], Optional[str], Optional[str]) -> Span
        self._restore_from_shutdown()

        return self.trace(name, service, resource, span_type)

    def shutdown(self, timeout=None):
        # type: (Optional[float]) -> None
        """Shutdown the tracer.

        This will stop the background writer/worker and flush any finished traces in the buffer. The tracer cannot be
        used for tracing after this method has been called. A new tracer instance is required to continue tracing.

        :param timeout: How long in seconds to wait for the background worker to flush traces
            before exiting or :obj:`None` to block until flushing has successfully completed (default: :obj:`None`)
        :type timeout: :obj:`int` | :obj:`float` | :obj:`None`
        """
        try:
            self._writer.stop(timeout=timeout)
        except service.ServiceStatusError:
            # It's possible the writer never got started in the first place :(
            pass

        with self._shutdown_lock:
            atexit.unregister(self._atexit)
            forksafe.unregister(self._child_after_fork)

            self.start_span = self._shutdown_start_span  # type: ignore[assignment]
            self.trace = self._shutdown_trace  # type: ignore[assignment]

    @staticmethod
    def _use_log_writer():
        # type: () -> bool
        """Returns whether the LogWriter should be used in the environment by
        default.

        The LogWriter required by default in AWS Lambdas when the Datadog Agent extension
        is not available in the Lambda.
        """
        if (
            environ.get("DD_AGENT_HOST")
            or environ.get("DATADOG_TRACE_AGENT_HOSTNAME")
            or environ.get("DD_TRACE_AGENT_URL")
        ):
            # If one of these variables are set, we definitely have an agent
            return False
        elif _in_aws_lambda() and _has_aws_lambda_agent_extension():
            # If the Agent Lambda extension is available then an AgentWriter is used.
            return False
        else:
            return _in_aws_lambda()

    @staticmethod
    def _use_sync_mode():
        # type: () -> bool
        """Returns, if an `AgentWriter` is to be used, whether it should be run
         in synchronous mode by default.

        There is only one case in which this is desirable:

        - AWS Lambdas can have the Datadog agent installed via an extension.
          When it's available traces must be sent synchronously to ensure all
          are received before the Lambda terminates.
        """
        return _in_aws_lambda() and _has_aws_lambda_agent_extension()

    @staticmethod
    def _is_span_internal(span):
        return not span.span_type or span.span_type in _INTERNAL_APPLICATION_SPAN_TYPES


def _has_aws_lambda_agent_extension():
    # type: () -> bool
    """Returns whether the environment has the AWS Lambda Datadog Agent
    extension available.
    """
    return os.path.exists("/opt/extensions/datadog-agent")


def _in_aws_lambda():
    # type: () -> bool
    """Returns whether the environment is an AWS Lambda.
    This is accomplished by checking if the AWS_LAMBDA_FUNCTION_NAME environment
    variable is defined.
    """
    return bool(environ.get("AWS_LAMBDA_FUNCTION_NAME", False))
