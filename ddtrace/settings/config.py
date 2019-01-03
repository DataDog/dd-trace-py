import copy
import inspect
import logging

from ..pin import Pin
from .http import HttpConfig
from .integration import IntegrationConfig, IntegrationConfigItem, IntegrationConfigItemBase

log = logging.getLogger(__name__)


class Config(object):
    """Configuration object that exposes an API to set and retrieve
    global settings for each integration. All integrations must use
    this instance to register their defaults, so that they're public
    available and can be updated by users.
    """
    def __init__(self):
        # use a dict as underlying storing mechanism
        self._config = {}
        self._http = HttpConfig()

    def __getattr__(self, name):
        if name not in self._config:
            self._config[name] = IntegrationConfig(self, name)
        return self._config[name]

    def get_from(self, obj):
        """Retrieves the configuration for the given object.
        Any object that has an attached `Pin` must have a configuration
        and if a wrong object is given, an empty `dict` is returned
        for safety reasons.
        """
        pin = Pin.get_from(obj)
        if pin is None:
            log.debug('No configuration found for %s', obj)
            return {}

        return pin._config

    def _add(self, integration, settings):
        """Internal API that registers an integration with given default settings.

        :param integration: The integration name (i.e. `requests`)
        :type integration: str
        :param settings: Either a class which defines `IntegrationConfigItem` properties
            or a dictionary containing settings
        :type settings: dict|class
        """
        # Make a copy of `settings`
        settings = copy.deepcopy(settings)

        # Fetch the existing integration settings (or a new `IntegrationConfig` if none exists)
        existing = getattr(self, integration)

        # If we have a class, then iterate it's properties looking for integration config items
        if inspect.isclass(settings):
            # Only collect properites which inherit from `IntegrationConfigItemBase`
            for key, value in settings.__dict__.items():
                if isinstance(value, IntegrationConfigItemBase):
                    existing[key] = value

        # Otherwise, assume `settings` is a `dict`
        else:
            for key, value in settings.items():
                # Ensure all items are `IntegrationConfigItem`s
                if not isinstance(value, IntegrationConfigItemBase):
                    value = IntegrationConfigItem(key, default=value)
                existing[key] = value

        # Update our integration settings
        self._config[integration] = existing

    def _register(self, integration):
        """
        Decorator factory helper for registering a class as an integration config

        Example::

            @config._register('flask')
            class FlaskConfig:
                service_name = IntegrationConfigItem('service_name', default='flask')


        This is the same as doing::

            class FlaskConfig:
                service_name = IntegrationConfigItem('service_name', default='flask')

            config._add('flask', FlaskConfig)


        :param integration: The name of the integration to register for
        :type integration: str
        :rtype: function
        :returns: decorator for wrapping the configuration object
        """
        def wrapper(settings):
            self._add(integration, settings)
        return wrapper

    def trace_headers(self, whitelist):
        """
        Registers a set of headers to be traced at global level or integration level.
        :param whitelist: the case-insensitive list of traced headers
        :type whitelist: list of str or str
        :return: self
        :rtype: HttpConfig
        """
        self._http.trace_headers(whitelist)
        return self

    def header_is_traced(self, header_name):
        """
        Returns whether or not the current header should be traced.
        :param header_name: the header name
        :type header_name: str
        :rtype: bool
        """
        return self._http.header_is_traced(header_name)

    def __repr__(self):
        cls = self.__class__
        integrations = ', '.join(self._config.keys())
        return '{}.{}({})'.format(cls.__module__, cls.__name__, integrations)
