import functools
import logging
from os import environ, getpid

from .ext import system
from .provider import DefaultContextProvider
from .context import Context
from .sampler import AllSampler, RateSampler, RateByServiceSampler
from .writer import AgentWriter
from .span import Span
from .constants import FILTERS_KEY, SAMPLE_RATE_METRIC_KEY
from . import compat
from .ext.priority import AUTO_REJECT, AUTO_KEEP


log = logging.getLogger(__name__)


class Tracer(object):
    """
    Tracer is used to create, sample and submit spans that measure the
    execution time of sections of code.

    If you're running an application that will serve a single trace per thread,
    you can use the global tracer instance::

        from ddtrace import tracer
        trace = tracer.trace("app.request", "web-server").finish()
    """
    DEFAULT_HOSTNAME = environ.get('DD_AGENT_HOST', environ.get('DATADOG_TRACE_AGENT_HOSTNAME', 'localhost'))
    DEFAULT_PORT = int(environ.get('DD_TRACE_AGENT_PORT', 8126))

    def __init__(self):
        """
        Create a new ``Tracer`` instance. A global tracer is already initialized
        for common usage, so there is no need to initialize your own ``Tracer``.
        """
        self.sampler = None
        self.priority_sampler = None

        # Apply the default configuration
        self.configure(
            enabled=True,
            hostname=self.DEFAULT_HOSTNAME,
            port=self.DEFAULT_PORT,
            sampler=AllSampler(),
            context_provider=DefaultContextProvider(),
        )

        # A hook for local debugging. shouldn't be needed or used in production
        self.debug_logging = False

        # globally set tags
        self.tags = {}

        # a buffer for service info so we dont' perpetually send the same things
        self._services = {}

    def get_call_context(self, *args, **kwargs):
        """
        Return the current active ``Context`` for this traced execution. This method is
        automatically called in the ``tracer.trace()``, but it can be used in the application
        code during manual instrumentation like::

            from ddtrace import import tracer

            async def web_handler(request):
                context = tracer.get_call_context()
                # use the context if needed
                # ...

        This method makes use of a ``ContextProvider`` that is automatically set during the tracer
        initialization, or while using a library instrumentation.
        """
        return self._context_provider.active(*args, **kwargs)

    @property
    def context_provider(self):
        """Returns the current Tracer Context Provider"""
        return self._context_provider

    def configure(self, enabled=None, hostname=None, port=None, sampler=None,
                  context_provider=None, wrap_executor=None, priority_sampling=None,
                  settings=None):
        """
        Configure an existing Tracer the easy way.
        Allow to configure or reconfigure a Tracer instance.

        :param bool enabled: If True, finished traces will be submitted to the API.
            Otherwise they'll be dropped.
        :param str hostname: Hostname running the Trace Agent
        :param int port: Port of the Trace Agent
        :param object sampler: A custom Sampler instance, locally deciding to totally drop the trace or not.
        :param object context_provider: The ``ContextProvider`` that will be used to retrieve
            automatically the current call context. This is an advanced option that usually
            doesn't need to be changed from the default value
        :param object wrap_executor: callable that is used when a function is decorated with
            ``Tracer.wrap()``. This is an advanced option that usually doesn't need to be changed
            from the default value
        :param priority_sampling: enable priority sampling, this is required for
            complete distributed tracing support.
        """
        if enabled is not None:
            self.enabled = enabled

        filters = None
        if settings is not None:
                filters = settings.get(FILTERS_KEY)

        if sampler is not None:
            self.sampler = sampler

        if priority_sampling:
            self.priority_sampler = RateByServiceSampler()

        if hostname is not None or port is not None or filters is not None or \
                priority_sampling is not None:
            # Preserve hostname and port when overriding filters or priority sampling
            default_hostname = self.DEFAULT_HOSTNAME
            default_port = self.DEFAULT_PORT
            if hasattr(self, 'writer') and hasattr(self.writer, 'api'):
                default_hostname = self.writer.api.hostname
                default_port = self.writer.api.port
            self.writer = AgentWriter(
                hostname or default_hostname,
                port or default_port,
                filters=filters,
                priority_sampler=self.priority_sampler,
            )

        if context_provider is not None:
            self._context_provider = context_provider

        if wrap_executor is not None:
            self._wrap_executor = wrap_executor

    def start_span(self, name, child_of=None, service=None, resource=None, span_type=None):
        """
        Return a span that will trace an operation called `name`. This method allows
        parenting using the ``child_of`` kwarg. If it's missing, the newly created span is a
        root span.

        :param str name: the name of the operation being traced.
        :param object child_of: a ``Span`` or a ``Context`` instance representing the parent for this span.
        :param str service: the name of the service being traced.
        :param str resource: an optional name of the resource being tracked.
        :param str span_type: an optional operation type.

        To start a new root span, simply::

            span = tracer.start_span("web.request")

        If you want to create a child for a root span, just::

            root_span = tracer.start_span("web.request")
            span = tracer.start_span("web.decoder", child_of=root_span)

        Or if you have a ``Context`` object::

            context = tracer.get_call_context()
            span = tracer.start_span("web.worker", child_of=context)
        """
        if child_of is not None:
            # retrieve if the span is a child_of a Span or a of Context
            child_of_context = isinstance(child_of, Context)
            context = child_of if child_of_context else child_of.context
            parent = child_of.get_current_span() if child_of_context else child_of
        else:
            context = Context()
            parent = None

        if parent:
            trace_id = parent.trace_id
            parent_span_id = parent.span_id
        else:
            trace_id = context.trace_id
            parent_span_id = context.span_id

        if trace_id:
            # child_of a non-empty context, so either a local child span or from a remote context

            # when not provided, inherit from parent's service
            if parent:
                service = service or parent.service

            span = Span(
                self,
                name,
                trace_id=trace_id,
                parent_id=parent_span_id,
                service=service,
                resource=resource,
                span_type=span_type,
            )

            # Extra attributes when from a local parent
            if parent:
                span.sampled = parent.sampled
                span._parent = parent

        else:
            # this is the root span of a new trace
            span = Span(
                self,
                name,
                service=service,
                resource=resource,
                span_type=span_type,
            )

            span.sampled = self.sampler.sample(span)
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
                    context.sampling_priority = 0

        # add common tags
        if self.tags:
            span.set_tags(self.tags)
        if not span._parent:
            span.set_tag(system.PID, getpid())

        # add it to the current context
        context.add_span(span)

        return span

    def trace(self, name, service=None, resource=None, span_type=None):
        """
        Return a span that will trace an operation called `name`. The context that created
        the span as well as the span parenting, are automatically handled by the tracing
        function.

        :param str name: the name of the operation being traced
        :param str service: the name of the service being traced. If not set,
                            it will inherit the service from its parent.
        :param str resource: an optional name of the resource being tracked.
        :param str span_type: an optional operation type.

        You must call `finish` on all spans, either directly or with a context
        manager::

            >>> span = tracer.trace("web.request")
                try:
                    # do something
                finally:
                    span.finish()

            >>> with tracer.trace("web.request") as span:
                    # do something

        Trace will store the current active span and subsequent child traces will
        become its children::

            parent = tracer.trace("parent")     # has no parent span
            child  = tracer.trace("child")      # is a child of a parent
            child.finish()
            parent.finish()

            parent2 = tracer.trace("parent2")   # has no parent span
            parent2.finish()
        """
        # retrieve the Context using the context provider and create
        # a new Span that could be a root or a nested span
        context = self.get_call_context()
        return self.start_span(
            name,
            child_of=context,
            service=service,
            resource=resource,
            span_type=span_type,
        )

    def current_root_span(self):
        """Returns the root span of the current context.

        This is useful for attaching information related to the trace as a
        whole without needing to add to child spans.

        Usage is simple, for example::

            # get the root span
            root_span = tracer.current_root_span()
            # set the host just once on the root span
            root_span.set_tag('host', '127.0.0.1')
        """
        return self.get_call_context().get_current_root_span()

    def current_span(self):
        """
        Return the active span for the current call context or ``None``
        if no spans are available.
        """
        return self.get_call_context().get_current_span()

    def record(self, context):
        """
        Record the given ``Context`` if it's finished.
        """
        # extract and enqueue the trace if it's sampled
        trace, sampled = context.get()
        if trace and sampled:
            self.write(trace)

    def write(self, spans):
        """
        Send the trace to the writer to enqueue the spans list in the agent
        sending queue.
        """
        if not spans:
            return  # nothing to do

        if self.debug_logging:
            log.debug("writing %s spans (enabled:%s)", len(spans), self.enabled)
            for span in spans:
                log.debug("\n%s", span.pprint())

        if self.enabled and self.writer:
            # only submit the spans if we're actually enabled (and don't crash :)
            self.writer.write(spans=spans)

    def set_service_info(self, service, app, app_type):
        """Set the information about the given service.

        :param str service: the internal name of the service (e.g. acme_search, datadog_web)
        :param str app: the off the shelf name of the application (e.g. rails, postgres, custom-app)
        :param str app_type: the type of the application (e.g. db, web)
        """
        try:
            # don't bother sending the same services over and over.
            info = (service, app, app_type)
            if self._services.get(service, None) == info:
                return
            self._services[service] = info

            if self.debug_logging:
                log.debug("set_service_info: service:%s app:%s type:%s", service, app, app_type)

            # If we had changes, send them to the writer.
            if self.enabled and self.writer:

                # translate to the form the server understands.
                services = {}
                for service, app, app_type in self._services.values():
                    services[service] = {"app": app, "app_type": app_type}

                # queue them for writes.
                self.writer.write(services=services)
        except Exception:
            log.debug("error setting service info", exc_info=True)

    def wrap(self, name=None, service=None, resource=None, span_type=None):
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
            # FIXME[matt] include the class name for methods.
            span_name = name if name else '%s.%s' % (f.__module__, f.__name__)

            # detect if the the given function is a coroutine to use the
            # right decorator; this initial check ensures that the
            # evaluation is done only once for each @tracer.wrap
            if compat.iscoroutinefunction(f):
                # call the async factory that creates a tracing decorator capable
                # to await the coroutine execution before finishing the span. This
                # code is used for compatibility reasons to prevent Syntax errors
                # in Python 2
                func_wrapper = compat.make_async_decorator(
                    self, f, span_name,
                    service=service,
                    resource=resource,
                    span_type=span_type,
                )
            else:
                @functools.wraps(f)
                def func_wrapper(*args, **kwargs):
                    # if a wrap executor has been configured, it is used instead
                    # of the default tracing function
                    if getattr(self, '_wrap_executor', None):
                        return self._wrap_executor(
                            self,
                            f, args, kwargs,
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
        """ Set some tags at the tracer level.
        This will append those tags to each span created by the tracer.

        :param dict tags: dict of tags to set at tracer level
        """
        self.tags.update(tags)
