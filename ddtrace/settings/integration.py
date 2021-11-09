import os
from typing import Optional
from typing import Tuple

from .._hooks import Hooks
from ..internal.utils.attrdict import AttrDict
from ..internal.utils.formats import asbool
from ..internal.utils.formats import get_env
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

    def _get_analytics_settings(self):
        # type: () -> Tuple[Optional[bool], float]
        # Set default analytics configuration, default is disabled
        # DEV: Default to `None` which means do not set this key
        # Inject environment variables for integration
        _ = os.environ.get("DD_TRACE_%s_ANALYTICS_ENABLED" % self.integration_name.upper()) or get_env(
            self.integration_name, "analytics_enabled"
        )
        analytics_enabled = asbool(_) if _ is not None else None

        analytics_sample_rate = float(
            os.environ.get("DD_TRACE_%s_ANALYTICS_SAMPLE_RATE" % self.integration_name.upper())
            or get_env(self.integration_name, "analytics_sample_rate")
            or 1.0
        )

        return analytics_enabled, analytics_sample_rate

    @property
    def trace_query_string(self):
        if self.http.trace_query_string is not None:
            return self.http.trace_query_string
        return self.global_config.http.trace_query_string

    @property
    def is_header_tracing_configured(self):
        # type: (...) -> bool
        """Returns whether header tracing is enabled for this integration.

        Will return true if traced headers are configured for this integration
        or if they are configured globally.
        """
        return self.http.is_header_tracing_configured or self.global_config.http.is_header_tracing_configured

    def header_is_traced(self, header_name):
        # type: (str) -> bool
        """
        Returns whether or not the current header should be traced.
        :param header_name: the header name
        :type header_name: str
        :rtype: bool
        """
        return self._header_tag_name(header_name) is not None

    def _header_tag_name(self, header_name):
        # type: (str) -> Optional[str]
        tag_name = self.http._header_tag_name(header_name)
        if tag_name is None:
            return self.global_config._header_tag_name(header_name)
        return tag_name

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
