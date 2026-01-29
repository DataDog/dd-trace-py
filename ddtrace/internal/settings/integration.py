import os
from typing import Optional  # noqa:F401

from ddtrace.internal.utils.attrdict import AttrDict
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.vendor.debtcollector import deprecate

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
        object.__setattr__(self, "http", HttpConfig())
        object.__setattr__(self, "hooks", Hooks())

        # Trace Analytics was removed in v3.0.0
        # TODO(munir): Remove all references to analytics_enabled and analytics_sample_rate
        self.setdefault("analytics_enabled", False)
        self.setdefault("analytics_sample_rate", 1.0)

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

    APP_ANALYTICS_CONFIG_NAMES = ("analytics_enabled", "analytics_sample_rate")

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
    def is_header_tracing_configured(self) -> bool:
        """Returns whether header tracing is enabled for this integration.

        Will return true if traced headers are configured for this integration
        or if they are configured globally.
        """
        return self.http.is_header_tracing_configured or self.global_config._http.is_header_tracing_configured

    def header_is_traced(self, header_name: str) -> bool:
        """Returns whether or not the current header should be traced."""
        return self._header_tag_name(header_name) is not None

    def _header_tag_name(self, header_name: str) -> Optional[str]:
        tag_name = self.http._header_tag_name(header_name)
        if tag_name is None:
            return self.global_config._header_tag_name(header_name)
        return tag_name

    def __getattr__(self, key):
        return super().__getattr__(key)

    def __setattr__(self, key, value):
        return super().__setattr__(key, value)

    def get_analytics_sample_rate(self, use_global_config=False):
        return 1

    def __repr__(self):
        cls = self.__class__
        keys = ", ".join(self.keys())
        return f"{cls.__module__}.{cls.__name__}({keys})"

    def copy(self):
        new_instance = self.__class__(self.global_config, self.integration_name)
        new_instance.update(self)
        return new_instance


class Hooks:
    """Deprecated no-op Hooks class for backwards compatibility."""

    def register(self, hook, func=None):
        deprecate(
            "Hooks.register() is deprecated and is currently a no-op.",
            message="To interact with spans, use get_current_span() or get_current_root_span().",
            removal_version="5.0.0",
            category=DDTraceDeprecationWarning,
        )
        if not func:
            # Return a no-op decorator
            def wrapper(func):
                return func

            return wrapper
        return None

    def on(self, hook, func=None):
        return self.register(hook, func)

    def deregister(self, hook, func):
        deprecate(
            "Hooks.deregister() is deprecated and is currently a no-op.",
            removal_version="5.0.0",
            category=DDTraceDeprecationWarning,
        )
        pass

    def emit(self, hook, *args, **kwargs):
        deprecate(
            "Hooks.emit() is deprecated",
            message="Use tracer.current_span() or TraceFilters to retrieve and/or modify spans",
            removal_version="5.0.0",
            category=DDTraceDeprecationWarning,
        )
        pass
