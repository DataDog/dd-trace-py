import sys
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from ddtrace._monkey import patch as patch
    from ddtrace._monkey import patch_all as patch_all
    from ddtrace.internal.settings._config import config as config
    from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning as DDTraceDeprecationWarning
    from ddtrace.trace import tracer as tracer
    from ddtrace.version import __version__ as __version__


LOADED_MODULES = frozenset(sys.modules)

import ddtrace.internal._unpatched  # noqa: E402, F401, isort:skip

__all__ = ["__version__", "patch", "patch_all", "config", "DDTraceDeprecationWarning"]

_EXPORTS = {
    "configure_ddtrace_logger": ("ddtrace._logger", "configure_ddtrace_logger"),
    "PYTHON_VERSION_INFO": ("ddtrace.internal.compat", "PYTHON_VERSION_INFO"),
    "is_at_least_py": ("ddtrace.internal.compat", "is_at_least_py"),
    "env": ("ddtrace.internal.settings", "env"),
    "deprecate": ("ddtrace.internal.utils.deprecations", "deprecate"),
    "__version__": ("ddtrace.version", "__version__"),
    "patch": ("ddtrace._monkey", "patch"),
    "patch_all": ("ddtrace._monkey", "patch_all"),
    "config": ("ddtrace.internal.settings._config", "config"),
    "DDTraceDeprecationWarning": ("ddtrace.internal.utils.deprecations", "DDTraceDeprecationWarning"),
    "check_supported_python_version": ("ddtrace._initialize", "check_supported_python_version"),
    # TODO: Remove root-level re-exports in v5.0.
    "tracer": ("ddtrace.trace", "tracer"),
}


def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    from ddtrace.internal.settings import env

    if env.get("_DD_GLOBAL_TRACER_INIT", "true").lower() not in ("1", "true"):
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    from importlib import import_module

    module_name, attr_name = _EXPORTS[name]
    module = import_module(module_name)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(_EXPORTS))
