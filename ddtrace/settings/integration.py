import os
from typing import Optional  # noqa:F401

from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.vendor.debtcollector import deprecate

from .._hooks import Hooks
from ..internal.utils.attrdict import AttrDict
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
        
        # Trace Analytics was removed in v3.0.0
        # TODO(munir): Remove all references to analytics_enabled and analytics_sample_rate
        self.setdefault("analytics_enabled", False)
        self.setdefault("analytics_sample_rate", 1.0)
        self._analytics_enabled = False
        self._analytics_sample_rate = 1.0

        service = os.getenv(
            "DD_%s_SERVICE" % name.upper(),
            default=os.getenv(
                "DD_%s_SERVICE_NAME" % name.upper(),
                default=None,
            ),
        )
        self.setdefault("service", service)
        # TODO[v1.0]: this is required for backwards compatibility since some
        # integrations use service_name instead of service. These should be
        # unified.
        self.setdefault("service_name", service)

        object.__setattr__(
            self,
            "http_tag_query_string",
            self.get_http_tag_query_string(getattr(self, "default_http_tag_query_string", None)),
        )

    def get_http_tag_query_string(self, value):
        if self.global_config._http_tag_query_string:
            dd_http_server_tag_query_string = value if value else os.getenv("DD_HTTP_SERVER_TAG_QUERY_STRING", "true")
            # If invalid value, will default to True
            return dd_http_server_tag_query_string.lower() not in ("false", "0")
        return False

    @property
    def trace_query_string(self):
        if self.http.trace_query_string is not None:
            return self.http.trace_query_string
        return self.global_config._http.trace_query_string

    @property
    def is_header_tracing_configured(self):
        # type: (...) -> bool
        """Returns whether header tracing is enabled for this integration.

        Will return true if traced headers are configured for this integration
        or if they are configured globally.
        """
        return self.http.is_header_tracing_configured or self.global_config._http.is_header_tracing_configured

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
    
    def __getattr__(self, key):
        if key in ["analytics_enabled", "analytics_sample_rate"]:
            deprecate(
                f"{key} is deprecated",
                message="Controlling ingestion via analytics is no longer supported. " 
                "See https://docs.datadoghq.com/tracing/legacy_app_analytics/"
                "?code-lang=python#migrate-to-the-new-configuration-options",
                category=DDTraceDeprecationWarning,
                removal_version="4.0.0",
            )
        return super().__getattr__(key)
    
    def __setattr__(self, key, value):
        if key in ["analytics_enabled", "analytics_sample_rate"]:
            deprecate(
                f"{key} is deprecated",
                message="Controlling ingestion via analytics is no longer supported. " 
                "See https://docs.datadoghq.com/tracing/legacy_app_analytics/"
                "?code-lang=python#migrate-to-the-new-configuration-options",
                category=DDTraceDeprecationWarning,
                removal_version="4.0.0",
            )
        return super().__setattr__(key,value)
    
    def get_analytics_sample_rate(self, use_global_config=False):
        """
        Returns analytics sample rate but only when integration-specific
        analytics configuration is enabled with optional override with global
        configuration
        """
        # Use `None` as a way to say that it was not defined,
        #   `False` would mean `0` which is a different thing
        deprecate(
            "get_analytics_sample_rate is deprecated",
            message="The method currently returns 1 always",
            category=DDTraceDeprecationWarning,
            removal_version="4.0.0",
        )
        return 1

    def __repr__(self):
        cls = self.__class__
        keys = ", ".join(self.keys())
        return "{}.{}({})".format(cls.__module__, cls.__name__, keys)

    def copy(self):
        new_instance = self.__class__(self.global_config, self.integration_name)
        new_instance.update(self)
        return new_instance
