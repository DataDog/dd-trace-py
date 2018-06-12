import logging
import opentracing
from opentracing import Format
from opentracing.ext.scope_manager import ThreadLocalScopeManager

from ddtrace import Tracer as DatadogTracer
from ddtrace.constants import FILTERS_KEY
from ddtrace.settings import ConfigException

from .propagation import HTTPPropagator
# from .scope_manager import ScopeManager
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
    keys.CONTEXT_PROVIDER: None,
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

        self._scope_manager = ThreadLocalScopeManager()

        self._tracer = DatadogTracer()
        self._tracer.configure(enabled=self._enabled,
                               hostname=self._config.get(keys.AGENT_HOSTNAME),
                               port=self._config.get(keys.AGENT_PORT),
                               sampler=self._config.get(keys.SAMPLER),
                               settings=self._config.get(keys.SETTINGS),
                               context_provider=self._config.get(keys.CONTEXT_PROVIDER),
                               priority_sampling=self._config.get(keys.PRIORITY_SAMPLING),
                               )
        self._propagators = {
            Format.HTTP_HEADERS: HTTPPropagator(),
            Format.TEXT_MAP: HTTPPropagator(),
        }

    @property
    def scope_manager(self):
        """"""
        return self._scope_manager

    @property
    def active_span(self):
        """"""
        pass

    def start_active_span(self, operation_name, child_of=None, references=None,
                          tags=None, start_time=None, ignore_active_span=False,
                          finish_on_close=True):
        """"""
        pass

    def start_span(self, operation_name=None, child_of=None, references=None,
                   tags=None, start_time=None, ignore_active_span=False):
        """Starts and returns a new span."""

        # attempt to get the parent span from the scope manager
        if child_of is None and not ignore_active_span:
            child_of = self._scope_manager.active

        # pull out the context if child_of is a span
        if isinstance(child_of, Span):
            child_of = child_of.context

        ddspan = self._tracer.start_span(name=operation_name, child_of=child_of)
        span = Span(self, child_of, operation_name)
        span._dd_span = ddspan
        return span

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
