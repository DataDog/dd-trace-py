import logging

from copy import deepcopy

from .pin import Pin
from .utils.merge import deepmerge


log = logging.getLogger(__name__)


class ConfigException(Exception):
    """Configuration exception when an integration that is not available
    is called in the `Config` object.
    """
    pass


class Config(object):
    """Configuration object that exposes an API to set and retrieve
    global settings for each integration. All integrations must use
    this instance to register their defaults, so that they're public
    available and can be updated by users.
    """
    def __init__(self):
        # use a dict as underlying storing mechanism
        self._config = {}
        self.http = HttpConfig()

    def __getattr__(self, name):
        if name not in self._config:
            self._config[name] = dict()
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

    def _add(self, integration, settings, merge=True):
        """Internal API that registers an integration with given default
        settings.

        :param str integration: The integration name (i.e. `requests`)
        :param dict settings: A dictionary that contains integration settings;
            to preserve immutability of these values, the dictionary is copied
            since it contains integration defaults.
        :param bool merge: Whether to merge any existing settings with those provided,
            or if we should overwrite the settings with those provided;
            Note: when merging existing settings take precedence.
        """
        # DEV: Use `getattr()` to call our `__getattr__` helper
        existing = getattr(self, integration)
        settings = deepcopy(settings)

        if merge:
            # DEV: This may appear backwards keeping `existing` as the "source" and `settings` as
            #   the "destination", but we do not want to let `_add(..., merge=True)` overwrite any
            #   existing settings
            #
            # >>> config.requests['split_by_domain'] = True
            # >>> config._add('requests', dict(split_by_domain=False))
            # >>> config.requests['split_by_domain']
            # True
            self._config[integration] = deepmerge(existing, settings)
        else:
            self._config[integration] = settings

    def __repr__(self):
        cls = self.__class__
        integrations = ', '.join(self._config.keys())
        return '{}.{}({})'.format(cls.__module__, cls.__name__, integrations)


class HttpConfig(object):
    """Configuration object that expose an API to set and retrieve both global and integration specific settings
    related to the http context.
    """

    _traced_headers = 'traced_headers'

    def __init__(self):
        self._integrations_config = {}
        self._global_config = {}

    def trace_headers(self, *args, **kwargs):
        """Registers a set of headers to be traced at global level or integration level.
        :param args: the list of headers names
        :type args: list of str
        :param integrations: if None this setting will apply to all the integrations, otherwise only to the specific
                             integration
        :type integrations: str or list of str
        :return: self
        :rtype: HttpConfig
        """
        normalized_header_names = list([header.strip().lower() for header in args])
        integrations = kwargs.get('integrations', None)
        normalized_integrations = [integrations] if isinstance(integrations, str) else integrations or []
        if not normalized_integrations:
            self._set_config_key(self._global_config, normalized_header_names, self._traced_headers)
        else:
            for integration in normalized_integrations:
                self._set_config_key(self._integrations_config, normalized_header_names, integration,
                                     self._traced_headers)
        return self

    def get_integration_traced_headers(self, integration):
        """Returns a set of headers that are set for tracing for the specified integration.
        :param integration: the integration to retrieve the list of traced headers for.
        :type integration: str
        :return: the set of activated headers for tracing
        :rtype: set of str
        """
        global_headers = self._global_config.get(self._traced_headers, [])
        integration_headers = self._integrations_config.get(integration, {}).get(self._traced_headers, [])
        return set(integration_headers + global_headers)

    @staticmethod
    def _set_config_key(config, value, *args):
        """Utility method to set a value at any dept in a dictionary"""
        current = config
        for level in args[:-1]:
            # we create dict until the expected level
            if not current.get(level, None):
                current[level] = {}
            current = current[level]
        current[args[-1]] = value
