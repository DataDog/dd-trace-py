import opentracing
import logging

from ddtrace import Tracer as DatadogTracer
from ddtrace.constants import FILTERS_KEY
from ddtrace.ext import AppTypes
from ddtrace.settings import ConfigException

from .settings import ConfigKeys as keys, config_invalid_keys

from .util import merge_dicts

log = logging.getLogger(__name__)

DEFAULT_CONFIG = {
    keys.AGENT_HOSTNAME: 'localhost',
    keys.AGENT_PORT: 8126,
    keys.DEBUG: False,
    keys.ENABLED: True,
    keys.GLOBAL_TAGS: {},
    keys.SERVICE_NAME: None,
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

    __slots__ = ['_enabled', '_debug', '_service_name', '_tracer']

    def __init__(self, config={}, service_name=None, scope_manager=None):
        # Merge the given config with the default into a new dict
        self._config = merge_dicts(DEFAULT_CONFIG, config)

        # Pull out commonly used properties for performance
        self._service_name = self._config.get(keys.SERVICE_NAME, None) or service_name
        self._enabled = self._config.get(keys.ENABLED)
        self._debug = self._config.get(keys.DEBUG)

        if self._debug:
            # Ensure there are no typos in any of the keys
            invalid_keys = config_invalid_keys(self._config)
            if invalid_keys:
                str_invalid_keys = ','.join(invalid_keys)
                raise ConfigException('invalid keys given (%s)'.format(str_invalid_keys))

        if not self._service_name:
            raise ConfigException('a service_name is required')

        self._tracer = DatadogTracer()

        self._tracer.configure(enabled=self._enabled,
                               hostname=self._config.get(keys.AGENT_HOSTNAME),
                               port=self._config.get(keys.AGENT_PORT),
                               sampler=self._config.get(keys.SAMPLER),
                               settings=self._config.get(keys.SETTINGS),
                               context_provider=self._config.get(keys.CONTEXT_PROVIDER),
                               priority_sampling=self._config.get(keys.PRIORITY_SAMPLING),
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
