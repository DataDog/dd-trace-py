from copy import deepcopy
import os

from ddtrace.vendor import debtcollector

from .._hooks import Hooks
from ..utils.attrdict import AttrDict
from ..utils.formats import asbool
from ..utils.formats import get_env
from .http import HttpConfig


class IntegrationConfig(AttrDict):
    """
    Integration specific configuration object.

    This is what you will get when you do::

        from ddtrace import config

        # This is an `IntegrationConfig`
        config.flask

        # `IntegrationConfig` supports both attribute and item accessors
        config.flask['service_name'] = 'my-service-name'
        config.flask.service_name = 'my-service-name'
    """

    def __init__(self, global_config, name, *args, **kwargs):
        """
        :param global_config:
        :type global_config: Config
        :param args:
        :param kwargs:
        """
        super(IntegrationConfig, self).__init__(*args, **kwargs)

        # Set internal properties for this `IntegrationConfig`
        # DEV: By-pass the `__setattr__` overrides from `AttrDict` to set real properties
        object.__setattr__(self, "global_config", global_config)
        object.__setattr__(self, "integration_name", name)
        object.__setattr__(self, "hooks", Hooks())
        object.__setattr__(self, "http", HttpConfig())

        analytics_enabled, analytics_sample_rate = self._get_analytics_settings()
        self.setdefault("analytics_enabled", analytics_enabled)
        self.setdefault("analytics_sample_rate", float(analytics_sample_rate))

        service = get_env(name, "service", default=get_env(name, "service_name", default=None))
        self.setdefault("service", service)
        # TODO[v1.0]: this is required for backwards compatibility since some
        # integrations use service_name instead of service. These should be
        # unified.
        self.setdefault("service_name", service)

    def __deepcopy__(self, memodict=None):
        new = IntegrationConfig(self.global_config, self.integration_name, deepcopy(dict(self), memodict))
        new.hooks = deepcopy(self.hooks, memodict)
        new.http = deepcopy(self.http, memodict)
        return new

    def copy(self):
        new = IntegrationConfig(self.global_config, self.integration_name, dict(self))
        new.hooks = self.hooks
        new.http = self.http
        return new

    @property
    def trace_query_string(self):
        if self.http.trace_query_string is not None:
            return self.http.trace_query_string
        return self.global_config.http.trace_query_string

    def header_is_traced(self, header_name):
        """
        Returns whether or not the current header should be traced.
        :param header_name: the header name
        :type header_name: str
        :rtype: bool
        """
        return (
            self.http.header_is_traced(header_name)
            if self.http.is_header_tracing_configured
            else self.global_config.header_is_traced(header_name)
        )

    def _get_analytics_settings(self):
        # We can have deprecated names for integrations used for analytics settings
        deprecated_name = self._deprecated_name if hasattr(self, "_deprecated_name") else None
        deprecated_analytics_enabled = (get_env(deprecated_name, "analytics_enabled") if deprecated_name else None) or (
            os.environ.get("DD_TRACE_%s_ANALYTICS_ENABLED" % deprecated_name.upper()) if deprecated_name else None
        )
        deprecated_analytics_sample_rate = (
            get_env(deprecated_name, "analytics_sample_rate") if deprecated_name else None
        ) or (
            os.environ.get("DD_TRACE_%s_ANALYTICS_SAMPLE_RATE" % deprecated_name.upper()) if deprecated_name else None
        )
        if deprecated_analytics_enabled or deprecated_analytics_sample_rate:
            debtcollector.deprecate(
                (
                    "*DBAPI2_ANALYTICS* environment variables are now deprecated, use integration-specific configuration."
                ),
                removal_version="0.49.0",
            )

        # Set default analytics configuration, default is disabled
        # DEV: Default to `None` which means do not set this key
        # Inject environment variables for integration
        analytics_enabled = (
            os.environ.get("DD_TRACE_%s_ANALYTICS_ENABLED" % self.integration_name.upper())
            or get_env(self.integration_name, "analytics_enabled")
            or deprecated_analytics_enabled
        )
        if analytics_enabled is not None:
            analytics_enabled = asbool(analytics_enabled)

        analytics_sample_rate = (
            os.environ.get("DD_TRACE_%s_ANALYTICS_SAMPLE_RATE" % self.integration_name.upper())
            or get_env(self.integration_name, "analytics_sample_rate")
            or deprecated_analytics_sample_rate
            or 1.0
        )
        if analytics_sample_rate is not None:
            analytics_sample_rate = float(analytics_sample_rate)

        return analytics_enabled, analytics_sample_rate

    def _is_analytics_enabled(self, use_global_config):
        # DEV: analytics flag can be None which should not be taken as
        # enabled when global flag is disabled
        if use_global_config and self.global_config.analytics_enabled:
            return self.analytics_enabled is not False
        else:
            return self.analytics_enabled is True

    def get_analytics_sample_rate(self, use_global_config=False):
        """
        Returns analytics sample rate but only when integration-specific
        analytics configuration is enabled with optional override with global
        configuration
        """
        if self._is_analytics_enabled(use_global_config):
            analytics_sample_rate = getattr(self, "analytics_sample_rate", None)
            # return True if attribute is None or attribute not found
            if analytics_sample_rate is None:
                return True
            # otherwise return rate
            return analytics_sample_rate

        # Use `None` as a way to say that it was not defined,
        #   `False` would mean `0` which is a different thing
        return None

    def __repr__(self):
        cls = self.__class__
        keys = ", ".join(self.keys())
        return "{}.{}({})".format(cls.__module__, cls.__name__, keys)
