import opentracing
import logging

from ddtrace import Tracer as DatadogTracer
from ddtrace.constants import FILTERS_KEY
from ddtrace.ext import AppTypes
from ddtrace.settings import ConfigException

from .constants import ConfigKeys as keys

from .util import merge_dicts

log = logging.getLogger(__name__)

DEFAULT_CONFIG = {
    keys.AGENT_HOSTNAME_KEY: 'localhost',
    keys.AGENT_PORT_KEY: 8126,
    keys.DEBUG_KEY: False,
    keys.ENABLED_KEY: True,
    keys.GLOBAL_TAGS_KEY: {},
    keys.SERVICE_NAME_KEY: None,
    keys.SAMPLER_KEY: None,
    keys.APP_TYPE_KEY: AppTypes.worker,
    keys.CONTEXT_PROVIDER_KEY: None,
    keys.PRIORITY_SAMPLING_KEY: None,
    keys.SETTINGS_KEY: {
        FILTERS_KEY: [],
    },
}


class Tracer(opentracing.Tracer):
    """A wrapper providing an OpenTracing API for the Datadog tracer."""

    __slots__ = ['_enabled', '_debug', '_service_name', '_tracer']

    def __init__(self, config={}, service_name=None, scope_manager=None):
        # Merge the given config with the default into a new dict
        self._config = merge_dicts(DEFAULT_CONFIG, config)

        # Pull out commonly used properties for performance
        self._service_name = self._config.get(keys.SERVICE_NAME_KEY, None) or service_name
        self._enabled = self._config.get(keys.ENABLED_KEY)
        self._debug = self._config.get(keys.DEBUG_KEY)

        if not self._service_name:
            raise ConfigException('a service_name is required')

        self._tracer = DatadogTracer()

        self._tracer.configure(enabled=self._enabled,
                               hostname=self._config.get(keys.AGENT_HOSTNAME_KEY),
                               port=self._config.get(keys.AGENT_PORT_KEY),
                               sampler=self._config.get(keys.SAMPLER_KEY),
                               settings=self._config.get(keys.SETTINGS_KEY),
                               context_provider=self._config.get(keys.CONTEXT_PROVIDER_KEY),
                               priority_sampling=self._config.get(keys.PRIORITY_SAMPLING_KEY),
                               )

    @property
    def scope_manager(self):
        """"""
        pass

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
        """"""
        pass

    def extract(self, span_context, format, carrier):
        """"""
        pass

def set_global_tracer(tracer):
    """Sets the global opentracer to the given tracer."""
    opentracing.tracer = tracer
