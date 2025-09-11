import sys
import os


LOADED_MODULES = frozenset(sys.modules.keys())


# Ensure we capture references to unpatched modules as early as possible
import ddtrace.internal._unpatched  # noqa

from ._lazy_init import (
    ensure_initialized,
    get_config,
    get_tracer,
    get_deprecation_warning,
    get_trace_module,
    get_ddtrace_submodule,
    setup_lazy_logger_hook,
    validate_logger_config,
)

# Set up lazy initialization
setup_lazy_logger_hook()
validate_logger_config()

from .version import get_version  # noqa: E402

__version__ = get_version()

# Check Python version synchronously to show deprecation warnings immediately
from ddtrace.vendor import debtcollector
from .internal.compat import PYTHON_VERSION_INFO
from .internal.utils.deprecations import DDTraceDeprecationWarning

if PYTHON_VERSION_INFO < (3, 8):
    deprecation_message = "Support for ddtrace with Python version %d.%d is deprecated and will be removed in 3.0.0."
    if PYTHON_VERSION_INFO < (3, 7):
        deprecation_message = "Support for ddtrace with Python version %d.%d was removed in 2.0.0."
    debtcollector.deprecate(
        (deprecation_message % (PYTHON_VERSION_INFO[0], PYTHON_VERSION_INFO[1])),
        category=DDTraceDeprecationWarning,
    )


@ensure_initialized
def patch(*args, **kwargs):
    """Lazy wrapper for patch function."""
    from ._monkey import patch as _patch

    return _patch(*args, **kwargs)


@ensure_initialized
def patch_all(**kwargs):
    """Lazy wrapper for patch_all function."""
    from ._monkey import patch_all as _patch_all

    return _patch_all(**kwargs)


# Build __all__ list, conditionally including tracer if environment variable says so
_base_all = [
    "patch",
    "patch_all",
    "config",
    "DDTraceDeprecationWarning",
]

# Add tracer to __all__ if it should be globally accessible
if os.environ.get("_DD_GLOBAL_TRACER_INIT", "true").lower() in ("1", "true"):
    __all__ = _base_all + ["tracer"]
else:
    __all__ = _base_all


# Module-level __getattr__ for lazy attribute access (Python 3.7+ feature)
def __getattr__(name: str):
    # Handle well-known special cases first
    if name == "config":
        return get_config()
    elif name == "DDTraceDeprecationWarning":
        return get_deprecation_warning()
    elif name == "tracer":
        # Lazy tracer access - initialize on first access regardless of environment variable
        return get_tracer()
    elif name == "trace":
        return get_trace_module()
    else:
        # Generic fallback: try to import any ddtrace submodule dynamically
        try:
            return get_ddtrace_submodule(name)
        except ImportError:
            # If import fails, raise AttributeError as expected for missing attributes
            raise AttributeError(f"module '{__name__}' has no attribute '{name}'")
