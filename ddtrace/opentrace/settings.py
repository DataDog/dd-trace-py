import opentracing

from ddtrace import Pin
from ddtrace.ext import AppTypes
from ddtrace.settings import ConfigException

from .tracer import Tracer


class Config(object):
    """ A configuration object used to configure your application for use with
    the OpenTracing Datadog python tracer.
    """

    __slots__ = ['_pin', '_config', '_service_name']

    def __init__(self, config, service_name=None, app=None,
                 app_type=AppTypes.worker):

        self._config = config
        self._service_name = config.get('service_name', service_name)

        if not self._service_name:
            raise ConfigException('a service_name is required')

    def create_tracer(self):
        """"""
        tracer = Tracer()

        # The Datadog python tracer requires a `Pin`.
        self._pin = Pin(service=self._service_name)
        return tracer

    def set_tracer(self):
        """ Create a new Tracer and set it to `opentracing.tracer`.  """
        tracer = self.create_tracer()

        opentracing.tracer = tracer

        return tracer

    @property
    def service_name(self):
        return self._service_name

