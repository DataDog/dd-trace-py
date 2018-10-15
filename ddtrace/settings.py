import logging
from copy import deepcopy
from .pin import Pin
from .utils.http import normalize_header_name
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
        self._config = {}

    def trace_headers(self, headers, integrations=None):
        """Registers a set of headers to be traced at global level or integration level.
        :param headers: the list of headers names
        :type headers: list of str
        :param integrations: if None this setting will apply to all the integrations, otherwise only to the specific
                             integration
        :type integrations: str or list of str
        :return: self
        :rtype: HttpConfig
        """
        headers = [headers] if isinstance(headers, str) else headers
        normalized_header_patterns = [normalize_header_name(header_name) for header_name in headers]
        if integrations is None:
            self._set_config_key(None, self._traced_headers, normalized_header_patterns)
            return self

        normalized_integrations = [integrations] if isinstance(integrations, str) else integrations
        for integration in normalized_integrations:
            self._set_config_key(integration, self._traced_headers, normalized_header_patterns)

        return self

    def get_integration_traced_headers(self, integration):
        """Returns a set of headers that are set for tracing for the specified integration.
        :param integration: the integration to retrieve the list of traced headers for.
        :type integration: str
        :return: the set of activated headers for tracing
        :rtype: set of str
        """
        global_headers = self._config[None][self._traced_headers] \
            if None in self._config and self._traced_headers in self._config[None] \
            else []
        integration_headers = self._config[integration][self._traced_headers] \
            if integration in self._config and self._traced_headers in self._config[integration] \
            else []

        return set(integration_headers + global_headers)

    def _set_config_key(self, integration, key, value):
        """Utility method to set a value at for a specific integration"""
        if integration not in self._config:
            self._config[integration] = {}
        self._config[integration][key] = value
