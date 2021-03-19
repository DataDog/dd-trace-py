from copy import deepcopy
import os

from ..internal.logger import get_logger
from ..pin import Pin
from ..utils.deprecation import get_service_legacy
from ..utils.formats import asbool
from ..utils.formats import get_env
from ..utils.formats import parse_tags_str
from .http import HttpConfig
from .integration import IntegrationConfig


log = get_logger(__name__)


# Borrowed from: https://stackoverflow.com/questions/20656135/python-deep-merge-dictionary-data#20666342
def _deepmerge(source, destination):
    """
    Merge the first provided ``dict`` into the second.

    :param dict source: The ``dict`` to merge into ``destination``
    :param dict destination: The ``dict`` that should get updated
    :rtype: dict
    :returns: ``destination`` modified
    """
    for key, value in source.items():
        if isinstance(value, dict):
            # get node or create one
            node = destination.setdefault(key, {})
            _deepmerge(value, node)
        else:
            destination[key] = value

    return destination


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
        # Master switch for turning on and off trace search by default
        # this weird invocation of get_env is meant to read the DD_ANALYTICS_ENABLED
        # legacy environment variable. It should be removed in the future
        legacy_config_value = get_env("analytics", "enabled", default=False)

        self.analytics_enabled = asbool(get_env("trace", "analytics_enabled", default=legacy_config_value))

        self.tags = parse_tags_str(os.getenv("DD_TAGS") or "")

        self.env = os.getenv("DD_ENV") or self.tags.get("env")
        # DEV: we don't use `self._get_service()` here because {DD,DATADOG}_SERVICE and
        # {DD,DATADOG}_SERVICE_NAME (deprecated) are distinct functionalities.
        self.service = os.getenv("DD_SERVICE") or os.getenv("DATADOG_SERVICE") or self.tags.get("service")
        self.version = os.getenv("DD_VERSION") or self.tags.get("version")
        self.http_server.error_statuses = "500-599"

        self.service_mapping = parse_tags_str(get_env("service", "mapping", default=""))

        # The service tag corresponds to span.service and should not be
        # included in the global tags.
        if self.service and "service" in self.tags:
            del self.tags["service"]

        # The version tag should not be included on all spans.
        if self.version and "version" in self.tags:
            del self.tags["version"]

        self.logs_injection = asbool(get_env("logs", "injection", default=False))

        self.report_hostname = asbool(get_env("trace", "report_hostname", default=False))

        self.health_metrics_enabled = asbool(get_env("trace", "health_metrics_enabled", default=False))

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
            log.debug("No configuration found for %s", obj)
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
            self._config[integration] = IntegrationConfig(self, integration, _deepmerge(existing, settings))
        else:
            self._config[integration] = IntegrationConfig(self, integration, settings)

    def trace_headers(self, whitelist):
        """
        Registers a set of headers to be traced at global level or integration level.
        :param whitelist: the case-insensitive list of traced headers
        :type whitelist: list of str or str
        :return: self
        :rtype: HttpConfig
        """
        self.http.trace_headers(whitelist)
        return self

    def header_is_traced(self, header_name):
        """
        Returns whether or not the current header should be traced.
        :param header_name: the header name
        :type header_name: str
        :rtype: bool
        """
        return self.http.header_is_traced(header_name)

    def _get_service(self, default=None):
        """
        Returns the globally configured service.

        If a service is not configured globally, attempts to get the service
        using the legacy environment variables via ``get_service_legacy``
        else ``default`` is returned.

        When support for {DD,DATADOG}_SERVICE_NAME is removed, all usages of
        this method can be replaced with `config.service`.

        :param default: the default service to use if none is configured or
            found.
        :type default: str
        :rtype: str|None
        """
        return self.service if self.service is not None else get_service_legacy(default=default)

    def __repr__(self):
        cls = self.__class__
        integrations = ", ".join(self._config.keys())
        return "{}.{}({})".format(cls.__module__, cls.__name__, integrations)
