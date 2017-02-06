import functools
import logging

from .context import ThreadLocalContext
from .sampler import AllSampler
from .span import Span
from .writer import AgentWriter


log = logging.getLogger(__name__)


class Tracer(object):
    """
    Tracer is used to create, sample and submit spans that measure the
    execution time of sections of code.

    If you're running an application that will serve a single trace per thread,
    you can use the global traced instance:

    >>> from ddtrace import tracer
    >>> trace = tracer.trace("app.request", "web-server").finish()
    """
    DEFAULT_HOSTNAME = 'localhost'
    DEFAULT_PORT = 7777

    def __init__(self):
        """
        Create a new tracer
        """
        # Apply the default configuration
        self.configure(
            enabled=True,
            hostname=self.DEFAULT_HOSTNAME,
            port=self.DEFAULT_PORT,
            sampler=AllSampler(),
        )

        # A hook for local debugging. shouldn't be needed or used in production
        self.debug_logging = False

        # globally set tags
        self.tags = {}

        # a buffer for service info so we dont' perpetually send the same things
        self._services = {}
        self._context = ThreadLocalContext()

    def get_call_context(self):
        """
        Returns the global context for this tracer. Returned ``Context`` must be thread-safe
        or thread-local.

        Mixin can be used to override this ``Tracer`` method so that the whole tracer is aware
        of the current execution mode (i.e. the ``Context`` retrieval is different in
        asynchronous environments).
        """
        return self._context.get()

    def configure(self, enabled=None, hostname=None, port=None, sampler=None):
        """
        Configure an existing Tracer the easy way.
        Allow to configure or reconfigure a Tracer instance.

        :param bool enabled: If True, finished traces will be
            submitted to the API. Otherwise they'll be dropped.
        :param str hostname: Hostname running the Trace Agent
        :param int port: Port of the Trace Agent
        :param object sampler: A custom Sampler instance
        """
        if enabled is not None:
            self.enabled = enabled

        if hostname is not None or port is not None:
            self.writer = AgentWriter(hostname or self.DEFAULT_HOSTNAME, port or self.DEFAULT_PORT)

        if sampler is not None:
            self.sampler = sampler

    def trace(self, name, service=None, resource=None, span_type=None, ctx=None, span_parent=None):
        """
        Return a span that will trace an operation called `name`. The context that generated
        the Span may be provided, as well as the current parent Span.

        :param str name: the name of the operation being traced
        :param str service: the name of the service being traced. If not set,
                            it will inherit the service from its parent.
        :param str resource: an optional name of the resource being tracked.
        :param str span_type: an optional operation type.

        :param Context ctx: TODO
        :param Span span_parent: TODO

        You must call `finish` on all spans, either directly or with a context
        manager.

        >>> span = tracer.trace("web.request")
            try:
                # do something
            finally:
                span.finish()
        >>> with tracer.trace("web.request") as span:
            # do something

        Trace will store the created span and subsequent child traces will
        become it's children.

        >>> tracer = Tracer()
        >>> parent = tracer.trace("parent")     # has no parent span
        >>> child  = tracer.trace("child")      # is a child of a parent
        >>> child.finish()
        >>> parent.finish()
        >>> parent2 = tracer.trace("parent2")   # has no parent span
        >>> parent2.finish()
        """
        # use the given Context object, or retrieve it using the Tracer logic
        # TODO: provide plain methods that don't do any automatic action
        context = ctx or self.get_call_context()
        parent = span_parent or context.get_current_span()

        if parent:
            # this is a child span
            span = Span(
                self,
                name,
                service=(service or parent.service),
                resource=resource,
                span_type=span_type,
                trace_id=parent.trace_id,
                parent_id=parent.span_id,
                ctx=context,
            )
            span._parent = parent
            span.sampled = parent.sampled
        else:
            # this is a root span
            span = Span(
                self,
                name,
                service=service,
                resource=resource,
                span_type=span_type,
                ctx=context,
            )
            self.sampler.sample(span)

        # add common tags
        if self.tags:
            span.set_tags(self.tags)

        # add it to context
        context.add_span(span)
        return span

    def current_span(self):
        """
        Return the current active span in this call Context or None.
        """
        return self.get_call_context().get_current_span()

    def record(self, span):
        """
        Record the given finished span.
        """
        # mark the span as finished for the current context
        context = span._context
        context.finish_span(span)

        if context.is_finished():
            # extract and enqueue the trace if it's sampled
            if span.sampled:
                trace = context.get_current_trace()
                self.write(trace)
            # reset the current context
            context.reset()

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
                    services[service] = {"app" : app, "app_type" : app_type}

                # queue them for writes.
                self.writer.write(services=services)
        except Exception:
            log.debug("error setting service info", exc_info=True)

    def wrap(self, name=None, service=None, resource=None, span_type=None, ctx=None, span_parent=None):
        """
        A decorator used to trace an entire function.

        :param str name: the name of the operation being traced. If not set,
                         defaults to the fully qualified function name.
        :param str service: the name of the service being traced. If not set,
                            it will inherit the service from it's parent.
        :param str resource: an optional name of the resource being tracked.
        :param str span_type: an optional operation type.
        :param Context context: the context to use.

        >>> @tracer.wrap('my.wrapped.function', service='my.service')
            def run():
                return 'run'
        >>> @tracer.wrap()  # name will default to 'execute' if unset
            def execute():
                return 'executed'

        You can access the parent span using `tracer.current_span()` to set
        tags:

        >>> @tracer.wrap()
            def execute():
                span = tracer.current_span()
                span.set_tag('a', 'b')
        """
        def wrap_decorator(f):
            # FIXME[matt] include the class name for methods.
            span_name = name if name else '%s.%s' % (f.__module__, f.__name__)

            @functools.wraps(f)
            def func_wrapper(*args, **kwargs):
                with self.trace(span_name, service=service, resource=resource,
                                span_type=span_type, ctx=ctx, span_parent=span_parent):
                    return f(*args, **kwargs)
            return func_wrapper

        return wrap_decorator

    def set_tags(self, tags):
        """ Set some tags at the tracer level.
        This will append those tags to each span created by the tracer.

        :param str tags: dict of tags to set at tracer level
        """
        self.tags.update(tags)
