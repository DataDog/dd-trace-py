from collections import ChainMap
from enum import Enum
import typing as t
from typing import Any
from typing import Optional

from envier import Env
from envier.env import EnvMeta
from envier.env import EnvVariable
from envier.env import _normalized

from ddtrace.internal.native import get_configuration_from_disk
from ddtrace.internal.settings._supported_configurations import CONFIGURATION_DEFAULTS
from ddtrace.internal.settings._supported_configurations import CONFIGURATION_TYPES
from ddtrace.internal.settings.env import dd_environ


FLEET_CONFIG, LOCAL_CONFIG, FLEET_CONFIG_IDS = get_configuration_from_disk()
ENV_CONFIG = dd_environ

_REGISTRY_TYPE_TO_PYTHON: dict[str, type] = {
    "boolean": bool,
    "int": int,
    "decimal": float,
    "string": str,
    "array": list,
    "map": dict,
}
_UNSET = object()


class _RegistryField:
    """Placeholder produced by :func:`field`; resolved into an ``EnvVariable`` by ``RegistryEnvMeta``."""

    __slots__ = ("suffix", "type", "default", "kwargs")

    def __init__(
        self,
        suffix: t.Optional[str] = None,
        *,
        type_: t.Any = _UNSET,
        default: t.Any = _UNSET,
        **kwargs: t.Any,
    ) -> None:
        self.suffix = suffix
        self.type = type_
        self.default = default
        self.kwargs = kwargs


def field(suffix: t.Optional[str] = None, **kwargs: t.Any) -> t.Any:
    """Declare a registry-backed configuration field.

    The variable's Python type and default are taken from the configuration
    registry (``supported-configurations.json``) at class-creation time, so they
    are never hand-duplicated. ``suffix`` overrides the env-var suffix when the
    attribute name differs from it (e.g. ``field("metrics.enabled")`` for an
    attribute named ``metrics``). ``type_``/``default`` may be overridden, and any
    other kwargs (``map``, ``validator``, ``parser``, ``help``) pass through to
    the underlying envier ``EnvVariable``.
    """
    return _RegistryField(suffix, **kwargs)


def _registry_default(reg_type: str, raw: t.Optional[str]) -> t.Any:
    if raw is None:
        return None
    if reg_type == "boolean":
        return raw.strip().lower() in ("1", "true", "yes", "on")
    if reg_type == "int":
        return int(raw)
    if reg_type == "decimal":
        return float(raw)
    if reg_type == "string":
        return raw
    if reg_type == "array":
        return [s.strip() for s in raw.split(",") if s.strip()] if raw else []
    if reg_type == "map":
        return {}
    raise ValueError(f"Unsupported registry type: {reg_type!r}")


def _resolve_registry_field(rf: "_RegistryField", prefix: str, attr_name: str) -> EnvVariable:
    suffix = rf.suffix if rf.suffix is not None else attr_name
    full = f"{_normalized(prefix)}_{_normalized(suffix)}" if prefix else _normalized(suffix)
    reg_type = CONFIGURATION_TYPES.get(full)
    if reg_type is None:
        raise ValueError(f"Registry-backed field {full!r} is not in supported-configurations.json")
    py_type = rf.type if rf.type is not _UNSET else _REGISTRY_TYPE_TO_PYTHON[reg_type]
    if rf.default is not _UNSET:
        default = rf.default
    else:
        default = _registry_default(reg_type, CONFIGURATION_DEFAULTS.get(full))
        if default is None and py_type is not type(None):
            py_type = t.Optional[py_type]
    return EnvVariable(py_type, suffix, default=default, **rf.kwargs)


class RegistryEnvMeta(EnvMeta):
    """Resolves :func:`field` placeholders into envier ``EnvVariable`` instances using the
    registry, before envier's ``EnvMeta`` applies prefixing. A no-op for classes that use
    no ``field()`` declarations.
    """

    def __new__(mcs, name: str, bases: tuple[type, ...], ns: dict[str, t.Any]) -> t.Any:
        prefix = ns.get("__prefix__", "")
        for key, val in list(ns.items()):
            if isinstance(val, _RegistryField):
                ns[key] = _resolve_registry_field(val, prefix, key)
        return super().__new__(mcs, name, bases, ns)


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


class DDConfig(Env, metaclass=RegistryEnvMeta):
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
