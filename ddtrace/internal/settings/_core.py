from collections import ChainMap
from enum import Enum
from typing import Any
from typing import Optional

from envier import Env

from ddtrace.internal.native import get_configuration_from_disk
from ddtrace.internal.settings.env import dd_environ


FLEET_CONFIG, LOCAL_CONFIG, FLEET_CONFIG_IDS = get_configuration_from_disk()
ENV_CONFIG = dd_environ


class ValueSource(str, Enum):
    FLEET_STABLE_CONFIG = "fleet_stable_config"
    ENV_VAR = "env_var"
    LOCAL_STABLE_CONFIG = "local_stable_config"
    CODE = "code"
    DEFAULT = "default"
    UNKNOWN = "unknown"
    OTEL_ENV_VAR = "otel_env_var"


class _ParsedValues:
    """Namespace of original *scalar* parsed config values, populated at initialization time.

    Holds only the flat ``EnvVariable`` and ``DerivedVariable`` items defined
    directly on the config class.  Nested sub-config instances are not included;
    their original values are accessible through the sub-config's own ``parsed``
    namespace instead::

        # correct
        config.parsed.enabled
        config.span.parsed.enabled

        # wrong – span is not a scalar and is not present here
        config.parsed.span.enabled
    """

    def __getattr__(self, name: str) -> Any:
        msg = f"No such configuration item: {name}"
        raise AttributeError(msg)


class DDConfig(Env):
    """Provides support for loading configurations from multiple sources."""

    def __init__(
        self,
        source: Optional[dict[str, str]] = None,
        parent: Optional["Env"] = None,
        dynamic: Optional[dict[str, str]] = None,
    ) -> None:
        self.fleet_source = FLEET_CONFIG
        self.local_source = LOCAL_CONFIG
        self.env_source = ENV_CONFIG

        # Order of precedence: provided source < local stable config < environment variables < fleet stable config
        full_source = ChainMap(self.fleet_source, self.env_source, self.local_source, source or {})

        # Parse the configuration and initialize the values
        super().__init__(source=full_source, parent=parent, dynamic=dynamic)

        # Shallow pass: cache each direct config item's parsed value before any
        # runtime overrides can be applied.  Nested sub-config instances handle
        # their own `parsed` namespace.
        self.parsed: _ParsedValues = _ParsedValues()
        for name, e in type(self).items(include_derived=True):
            setattr(self.parsed, name, getattr(self, name))

        # Initialize the value sources
        self._value_source = {}

        for name, e in type(self).items(recursive=True):
            if e.private:
                continue

            env_name = e.full_name

            # Get the item value recursively
            env_val = self
            for p in name.split("."):
                env_val = getattr(env_val, p)

            if env_name in self.fleet_source:
                value_source = ValueSource.FLEET_STABLE_CONFIG
            elif env_name in self.env_source and env_name.upper().startswith("OTEL_"):
                value_source = ValueSource.OTEL_ENV_VAR
            elif env_name in self.env_source:
                value_source = ValueSource.ENV_VAR
            elif env_name in self.local_source:
                value_source = ValueSource.LOCAL_STABLE_CONFIG
            elif env_name in self.source:
                value_source = ValueSource.CODE
            else:
                # No external source provided this key.  If e.default were NoDefault,
                # _retrieve() would have raised KeyError and __init__ would have failed.
                # So reaching here means the config is using its declared default value.
                value_source = ValueSource.DEFAULT

            self._value_source[env_name] = value_source

            if value_source == ValueSource.FLEET_STABLE_CONFIG:
                self.config_id = FLEET_CONFIG_IDS.get(env_name)
            else:
                self.config_id = None

    def value_source(self, env_name: str) -> str:
        return self._value_source.get(env_name, ValueSource.UNKNOWN)

    def dump_settings(self) -> dict[str, Any]:
        """Return a {dotted_name: value} snapshot of this config tree,
        including ``private=True`` entries.

        Keys are the dotted Python config path relative to this config root
        (e.g. ``stack.adaptive_sampling``, ``upload_interval``). The config
        ``__prefix__`` is deliberately *not* included, because callers
        typically wrap the dict under a channel-specific header (e.g.
        ``info.profiler.settings``) that already conveys the scope. Values
        are coerced to JSON-friendly types (bool/int/float/str/None/list/
        dict); anything exotic is stringified via ``repr``.

        Use this when you need to publish the effective configuration on a
        per-event channel (e.g. the profiler's per-profile ``info`` field).
        For process-level telemetry, use
        :func:`ddtrace.internal.telemetry.report_configuration`, which skips
        private items.
        """
        settings: dict[str, Any] = {}
        for name, _ in type(self).items(recursive=True):
            env_val = self
            for p in name.split("."):
                env_val = getattr(env_val, p)

            settings[name] = _json_safe_value(env_val)
        return settings


def _json_safe_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe_value(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _json_safe_value(v) for k, v in value.items()}
    return repr(value)
