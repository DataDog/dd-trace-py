import copy
import sys
from typing import Any
from typing import Optional

from envier.env import DerivedVariable
from envier.env import EnvVariable

from ddtrace.internal.settings import env
from ddtrace.internal.utils.attrdict import AttrDict
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.internal.utils.deprecations import deprecate

from ._core import DDConfig
from .http import HttpConfig


# Populated automatically by IntegrationEnvConfig.__init__ below, keyed by integration name. Not the
# tracer-wide Config singleton's own `_integration_configs` cache -- this module is a leaf
# `ddtrace.internal.settings._config` already imports, so it can't import `_config.py` back to write
# into that cache directly (the same constraint documented on IntegrationEnvConfig itself). Instead,
# `_config.py`'s own `Config.__getattr__` (which already imports this module for `IntegrationEnvConfig`)
# reads from this dict as a fallback, so a migrated integration's config becomes visible as
# `config.<name>` automatically, the moment its class is instantiated -- no explicit registration call
# needed anywhere, regardless of what imported the plugin's patch.py (IntegrationRegistry, the legacy
# _monkey.py path, or a test importing it directly).
_pending_plugin_configs: dict[str, "IntegrationEnvConfig"] = {}


def _integration_env_var_id(name: str) -> str:
    """Build the env var identifier portion for an integration name.

    Hyphens are not valid POSIX identifiers, so normalize them to underscores
    so env vars are usable from shells. Used to derive ``DD_<id>_SERVICE``,
    ``DD_TRACE_<id>_ENABLED``, etc. from an integration name.
    """
    return name.upper().replace("-", "_")


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
        super().__init__(*args, **kwargs)

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

        env_var_id = _integration_env_var_id(name)
        service = env.get(f"DD_{env_var_id}_SERVICE", default=None)
        self.setdefault("service", service)
        self.setdefault("service_name", service)

        object.__setattr__(
            self,
            "http_tag_query_string",
            self.get_http_tag_query_string(getattr(self, "default_http_tag_query_string", None)),
        )

    APP_ANALYTICS_CONFIG_NAMES = ("analytics_enabled", "analytics_sample_rate")

    def get_http_tag_query_string(self, value):
        if self.global_config._http_tag_query_string:
            dd_http_server_tag_query_string = value if value else env.get("DD_HTTP_SERVER_TAG_QUERY_STRING", "true")
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


class IntegrationEnvConfig(DDConfig):
    """envier-based per-integration configuration for a migrated integration. Declares only
    ``service`` plus enough Mapping-style compatibility for existing `config.<name>` callers.

    Category-specific config (HTTP tracing, distributed tracing propagation) lives in mixins under
    `ddtrace/_trace/settings.py`; mix those into a plugin's own leaf config class alongside this
    base.

    Only ever subclass this directly from that one leaf class (see
    `ddtrace/contrib/internal/urllib3/patch.py`'s `_Urllib3Config`), never from a shared
    intermediate base: subclassing alone triggers `__init_subclass__` below, which derives
    `__prefix__` from the integration's own `name`, moves this class to the end of the subclass's
    own bases so mixins listed in any order still get their `__init__` run (see `__init_subclass__`
    for why), flattens envier fields inherited from any mixin into the subclass's own `__dict__`
    (envier only resolves fields from there, not the MRO), and constructs the one instance the
    plugin needs.

    Self-registers into `_pending_plugin_configs` on construction, so `config.<name>` becomes valid
    the moment the class statement finishes executing -- no explicit `config = _MyConfig()`
    registration line needed.
    """

    service = DDConfig.v(Optional[str], "service", default=None)
    # setdefault-style alias for `service`: same default, independently overridable afterward (e.g.
    # by tests.utils.override_config), same as IntegrationConfig's `service`/`service_name` pair --
    # not a second, independently configurable env var.
    service_name = DDConfig.d(Optional[str], lambda c: c.service)

    # Deliberately not Mapping-like (no __contains__/__getitem__/__setitem__/get/update), unlike
    # IntegrationConfig (AttrDict-based). Shared helpers (ddtrace/contrib/internal/trace_utils.py)
    # and test helpers (tests.utils.override_config) have been updated to use plain attribute access
    # (getattr/setattr) instead, which works identically against both config types -- see those
    # call sites rather than reproducing dict-style access here.

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Runs once, automatically, the moment a leaf plugin config class's own class statement
        finishes executing -- this is "the decorator", just triggered by inheritance instead of by
        `@integration_config`. See the class docstring for why only a leaf class should ever trigger
        this (never a shared intermediate base).
        """
        super().__init_subclass__(**kwargs)

        module = sys.modules[cls.__module__]
        try:
            integration_name = module.name
        except AttributeError:
            raise TypeError(
                f'{cls.__module__} must declare `name = "<integration>"` before its IntegrationEnvConfig subclass'
            )

        # Ensure IntegrationEnvConfig ends up last among cls's own bases, regardless of the order a
        # plugin declared them in. envier's Env.__init__ (which this ultimately chains to via
        # DDConfig) never calls super().__init__() itself, so cooperative __init__ chaining stops
        # dead the moment it's reached -- any mixin listed *after* IntegrationEnvConfig in the
        # declared bases (e.g. `class C(IntegrationEnvConfig, HttpIntegrationConfigMixin)`) would
        # have its own __init__ silently skipped (HttpIntegrationConfigMixin's, which sets
        # `self.http`, is exactly this case). Reassigning __bases__ makes Python recompute the MRO,
        # so a plugin's own leaf class can list mixins in whatever order reads best.
        bases = cls.__bases__
        if bases[-1] is not IntegrationEnvConfig and IntegrationEnvConfig in bases:
            cls.__bases__ = tuple(b for b in bases if b is not IntegrationEnvConfig) + (IntegrationEnvConfig,)

        # Flatten envier fields inherited from any base (IntegrationEnvConfig itself, or a mixin like
        # DistributedTracingConfigMixin) into cls's own __dict__ -- envier's Env.__init__ resolves
        # fields from self.__class__.__dict__ only, not the MRO, so an inherited field would otherwise
        # resolve to the raw EnvVariable/DerivedVariable descriptor rather than its parsed value.
        for base in reversed(cls.__mro__[1:]):
            for attr_name, attr_value in vars(base).items():
                if isinstance(attr_value, (EnvVariable, DerivedVariable)) and attr_name not in cls.__dict__:
                    setattr(cls, attr_name, copy.copy(attr_value))

        if "__prefix__" not in cls.__dict__:
            cls.__prefix__ = f"DD_{_integration_env_var_id(integration_name)}"
        cls.__integration_name__ = integration_name

        if integration_name in _pending_plugin_configs:
            raise TypeError(
                f"{cls.__module__}: a config for integration '{integration_name}' was already "
                "instantiated -- only one IntegrationEnvConfig subclass is allowed per integration"
            )
        cls()  # constructs and self-registers; see __init__ below

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        _pending_plugin_configs[self.integration_name] = self

    @property
    def integration_name(self) -> str:
        return self.__integration_name__  # set by __init_subclass__ above
