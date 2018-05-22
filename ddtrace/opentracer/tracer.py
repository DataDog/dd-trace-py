import opentracing

from ddtrace.settings import ConfigException
from ddtrace.ext import AppTypes

from .util import merge_dicts


DEFAULT_CONFIG = {
    'agent_hostname': 'localhost',
    'agent_port': 8126,
    'debug': False,
    'enabled': True,
    'global_tags': {},
    'service_name': None,
    'sample_rate': 1,
    'app_type': AppTypes.worker,
}


class Tracer(opentracing.Tracer):
    """A wrapper providing an OpenTracing API for the Datadog tracer."""

    __slots__ = ['_enabled', '_debug', '_service_name']

    def __init__(self, config={}, service_name=None, scope_manager=None):
        # Merge the given config with the default into a new dict
        self._config = merge_dicts(DEFAULT_CONFIG, config)

        # Pull out commonly used properties for performance
        self._service_name = self._config.get('service_name', None) or service_name
        self._enabled = self._config.get('enabled', None) or DEFAULT_CONFIG['enabled']
        self._debug = self._config.get('debug', None) or DEFAULT_CONFIG['debug']

        if not self._service_name:
            raise ConfigException('a service_name is required')

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
