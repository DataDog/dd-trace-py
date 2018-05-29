import logging
import opentracing
from opentracing import Format

from ddtrace import Tracer as DatadogTracer
from ddtrace.constants import FILTERS_KEY
from ddtrace.ext import AppTypes
from ddtrace.settings import ConfigException

from .propagation import HTTPPropagator
from .scope_manager import ScopeManager
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
    keys.APP_TYPE: AppTypes.worker,
    keys.CONTEXT_PROVIDER: None,
    keys.PRIORITY_SAMPLING: None,
    keys.SETTINGS: {
        FILTERS_KEY: [],
    },
}


class Tracer(opentracing.Tracer):
    """A wrapper providing an OpenTracing API for the Datadog tracer."""

    __slots__ = ['_enabled', '_debug', '_service_name', '_tracer',
                 '_scope_manager']

    def __init__(self, service_name=None, config={}, scope_manager=None):
        # Merge the given config with the default into a new dict
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

        self._scope_manager = ScopeManager()

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
        """"""
        pass

    def inject(self, span_context, format, carrier):
        """Injects a span context into a carrier.

        :param span_context: span context to inject.

        :param format: format to encode the span context with.

        :param carrier: the carrier of the encoded span context.
        """
        if not isinstance(carrier, dict):
            raise opentracing.InvalidCarrierException('carrier is not a dict')

        propagator = self._propagators.get(format, None)

        if propagator is None:
            raise opentracing.UnsupportedFormatException

        propagator.inject(span_context, carrier)

    def extract(self, format, carrier):
        """Extracts a span context from a carrier.

        :param format: format that the carrier is encoded with.

        :param carrier: the carrier to extract from.
        """
        if not isinstance(carrier, dict):
            raise opentracing.InvalidCarrierException('carrier is not a dict')

        propagator = self._propagators.get(format, None)
        if propagator is None:
            raise opentracing.UnsupportedFormatException

        return propagator.extract(carrier)

def set_global_tracer(tracer):
    """Sets the global opentracer to the given tracer."""
    opentracing.tracer = tracer
