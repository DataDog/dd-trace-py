import logging
import opentracing
from opentracing import Format
from opentracing.ext.scope_manager import ThreadLocalScopeManager

from ddtrace import Tracer as DatadogTracer
from ddtrace.constants import FILTERS_KEY
from ddtrace.settings import ConfigException

from .propagation import HTTPPropagator
from .span import Span
from .span_context import SpanContext
from .settings import ConfigKeys as keys, config_invalid_keys
from .util import merge_dicts

log = logging.getLogger(__name__)

DEFAULT_CONFIG = {
    keys.AGENT_HOSTNAME: 'localhost',
    keys.AGENT_PORT: 8126,
    keys.DEBUG: False,
    keys.ENABLED: True,
    keys.GLOBAL_TAGS: {},
    keys.SAMPLER: None,
    keys.PRIORITY_SAMPLING: None,
    keys.SETTINGS: {
        FILTERS_KEY: [],
    },
}


class Tracer(opentracing.Tracer):
    """A wrapper providing an OpenTracing API for the Datadog tracer."""

    def __init__(self, service_name=None, config=None, scope_manager=None):
        # Merge the given config with the default into a new dict
        config = config or {}
        self._config = merge_dicts(DEFAULT_CONFIG, config)

        # Pull out commonly used properties for performance
        self._service_name = service_name
        self._enabled = self._config.get(keys.ENABLED)
        self._debug = self._config.get(keys.DEBUG)

        if self._debug:
            # Ensure there are no typos in any of the keys
            invalid_keys = config_invalid_keys(self._config)
            if invalid_keys:
                str_invalid_keys = ','.join(invalid_keys)
                raise ConfigException('invalid key(s) given (%s)'.format(str_invalid_keys))

        # TODO: we should set a default reasonable `service_name` (__name__) or
        # similar.
        if not self._service_name:
            raise ConfigException('a service_name is required')

        # default to using a threadlocal scope manager
        # TODO: should this be some kind of configuration option?
        self._scope_manager = scope_manager or ThreadLocalScopeManager()

        self._dd_tracer = DatadogTracer()
        self._dd_tracer.configure(enabled=self._enabled,
                                  hostname=self._config.get(keys.AGENT_HOSTNAME),
                                  port=self._config.get(keys.AGENT_PORT),
                                  sampler=self._config.get(keys.SAMPLER),
                                  settings=self._config.get(keys.SETTINGS),
                                  priority_sampling=self._config.get(keys.PRIORITY_SAMPLING),
                                  )
        self._propagators = {
            Format.HTTP_HEADERS: HTTPPropagator(),
            Format.TEXT_MAP: HTTPPropagator(),
        }

    @property
    def scope_manager(self):
        """Returns the scope manager being used by this tracer."""
        return self._scope_manager

    @property
    def active_span(self):
        """Gets the active span from the scope manager or none if it does not exist."""
        scope = self._scope_manager.active
        return scope.span if scope else None

    def start_active_span(self, operation_name, child_of=None, references=None,
                          tags=None, start_time=None, ignore_active_span=False,
                          finish_on_close=True):
        """Returns a newly started and activated `Scope`.
        The returned `Scope` supports with-statement contexts. For example:
            with tracer.start_active_span('...') as scope:
                scope.span.set_tag('http.method', 'GET')
                do_some_work()
            # Span.finish() is called as part of Scope deactivation through
            # the with statement.
        It's also possible to not finish the `Span` when the `Scope` context
        expires:
            with tracer.start_active_span('...',
                                          finish_on_close=False) as scope:
                scope.span.set_tag('http.method', 'GET')
                do_some_work()
            # Span.finish() is not called as part of Scope deactivation as
            # `finish_on_close` is `False`.
        :param operation_name: name of the operation represented by the new
            span from the perspective of the current service.
        :param child_of: (optional) a Span or SpanContext instance representing
            the parent in a REFERENCE_CHILD_OF Reference. If specified, the
            `references` parameter must be omitted.
        :param references: (optional) a list of Reference objects that identify
            one or more parent SpanContexts. (See the Reference documentation
            for detail).
        :param tags: an optional dictionary of Span Tags. The caller gives up
            ownership of that dictionary, because the Tracer may use it as-is
            to avoid extra data copying.
        :param start_time: an explicit Span start time as a unix timestamp per
            time.time().
        :param ignore_active_span: (optional) an explicit flag that ignores
            the current active `Scope` and creates a root `Span`.
        :param finish_on_close: whether span should automatically be finished
            when `Scope.close()` is called.
         :return: a `Scope`, already registered via the `ScopeManager`.
        """
        span = self.start_span(
            operation_name=operation_name,
            child_of=child_of,
            references=references,
            tags=tags,
            start_time=start_time,
            ignore_active_span=ignore_active_span,
        )
        scope = self._scope_manager.activate(span, finish_on_close)
        return scope

    def start_span(self, operation_name=None, child_of=None, references=None,
                   tags=None, start_time=None, ignore_active_span=False):
        """Starts and returns a new Span representing a unit of work.

        Starting a root Span (a Span with no causal references)::
            tracer.start_span('...')

        Starting a child Span (see also start_child_span())::
            tracer.start_span(
                '...',
                child_of=parent_span)
        Starting a child Span in a more verbose way::
            tracer.start_span(
                '...',
                references=[opentracing.child_of(parent_span)])
        :param operation_name: name of the operation represented by the new
            span from the perspective of the current service.
        :param child_of: (optional) a Span or SpanContext instance representing
            the parent in a REFERENCE_CHILD_OF Reference. If specified, the
            `references` parameter must be omitted.
        :param references: (optional) a list of Reference objects that identify
            one or more parent SpanContexts. (See the Reference documentation
            for detail)
        :param tags: an optional dictionary of Span Tags. The caller gives up
            ownership of that dictionary, because the Tracer may use it as-is
            to avoid extra data copying.
        :param start_time: an explicit Span start time as a unix timestamp per
            time.time()
        :param ignore_active_span: an explicit flag that ignores the current
            active `Scope` and creates a root `Span`.
        :return: an already-started Span instance.
        """
        ot_parent = child_of      # 'ot_parent' is more readable than 'child_of'
        ot_parent_context = None  # the parent span's context
        dd_parent = None          # the child_of to pass to the ddtracer

        if references and isinstance(references, list):
            # we currently only support child_of relations to one span
            ot_parent = references[0].referenced_context

        # Okay so here's the deal for ddtracer.start_span:
        #  - whenever child_of is not None ddspans with parent-child relationships
        #    will share a ddcontext which maintains a hierarchy of ddspans for
        #    the execution flow
        #  - when child_of is a ddspan then the ddtracer uses this ddspan to create
        #    the child ddspan
        #  - when child_of is a ddcontext then the ddtracer uses the ddcontext to
        #    get_current_span() for the parent
        if ot_parent is None and not ignore_active_span:
            # attempt to get the parent span from the scope manager
            scope = self._scope_manager.active
            parent_span = getattr(scope, 'span', None)
            ot_parent_context = getattr(parent_span, 'context', None)
            # we want the ddcontext of the active span in order to maintain the
            # ddspan hierarchy
            dd_parent = getattr(ot_parent_context, '_dd_context', None)
        elif ot_parent is not None and isinstance(ot_parent, Span):
            # a span is given to use as a parent
            ot_parent_context = ot_parent.context
            dd_parent = ot_parent._dd_span
        elif ot_parent is not None and isinstance(ot_parent, SpanContext):
            # a span context is given to use to find the parent ddspan
            dd_parent = ot_parent._dd_context
        elif ot_parent is None:
            # user wants to create a new parent span we don't have to do anything
            pass
        else:
            raise TypeError('invalid span configuration given')

        # create a new otspan and ddspan using the ddtracer and associate it with the new otspan
        otspan = Span(self, ot_parent_context, operation_name)
        ddspan = self._dd_tracer.start_span(name=operation_name, child_of=dd_parent)
        ddspan.start = start_time or ddspan.start  # set the start time if one is specified
        if tags is not None:
            ddspan.set_tags(tags)
        otspan._add_dd_span(ddspan)

        # activate this new span
        self._scope_manager.activate(otspan, False)
        return otspan

    def inject(self, span_context, format, carrier):
        """Injects a span context into a carrier.

        :param span_context: span context to inject.
        :param format: format to encode the span context with.
        :param carrier: the carrier of the encoded span context.
        """
        propagator = self._propagators.get(format, None)

        if propagator is None:
            raise opentracing.UnsupportedFormatException

        propagator.inject(span_context, carrier)

    def extract(self, format, carrier):
        """Extracts a span context from a carrier.

        :param format: format that the carrier is encoded with.
        :param carrier: the carrier to extract from.
        """
        propagator = self._propagators.get(format, None)

        if propagator is None:
            raise opentracing.UnsupportedFormatException

        return propagator.extract(carrier)


def set_global_tracer(tracer):
    """Sets the global opentracer to the given tracer."""
    opentracing.tracer = tracer
