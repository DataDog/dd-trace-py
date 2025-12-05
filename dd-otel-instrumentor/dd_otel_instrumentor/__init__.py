import os
from typing import Dict

# Enable OTel DD instrumentation mode by default when using this package
os.environ.setdefault("EXPERIMENTAL_OTEL_DD_INSTRUMENTATION_ENABLED", "true")

# Import and initialize the OTel trace handlers
from ddtrace.contrib.compat.otel import trace_handlers  # noqa: F401

__version__ = "0.1.0"

# Registry of available integrations and their patch modules
_INTEGRATIONS: Dict[str, str] = {
    "httpx": "ddtrace.contrib.internal.httpx.patch",
}


def patch(*integrations: str) -> None:
    """
    Patch one or more integrations to use OTel-compatible instrumentation.
    Args:
        *integrations: Names of integrations to patch (e.g., "httpx", "requests")
    Example:
        from dd_otel_instrumentor import patch
        # Patch a single integration
        patch("httpx")
        # Patch multiple integrations
        patch("httpx", "requests", "flask")
    Raises:
        ValueError: If an unknown integration name is provided
        ImportError: If the integration's dependencies are not installed
    """
    for name in integrations:
        _patch_integration(name)


def _patch_integration(name: str) -> None:
    """Patch a single integration by name."""
    if name not in _INTEGRATIONS:
        available = ", ".join(sorted(_INTEGRATIONS.keys()))
        raise ValueError(f"Unknown integration: {name!r}. Available integrations: {available}")

    module_path = _INTEGRATIONS[name]

    try:
        # Import the integration module
        import importlib

        module = importlib.import_module(module_path)

        # Call its patch function
        if hasattr(module, "patch"):
            module.patch()
        else:
            raise AttributeError(f"Integration module {module_path} has no 'patch' function")

    except ImportError as e:
        raise ImportError(
            f"Failed to import integration {name!r}. "
            f"Make sure its dependencies are installed: pip install dd-otel-instrumentor[{name}]"
        ) from e


def unpatch(*integrations: str) -> None:
    """
    Unpatch one or more integrations.
    Args:
        *integrations: Names of integrations to unpatch
    """
    for name in integrations:
        _unpatch_integration(name)


def _unpatch_integration(name: str) -> None:
    """Unpatch a single integration by name."""
    if name not in _INTEGRATIONS:
        return

    module_path = _INTEGRATIONS[name]

    try:
        import importlib

        module = importlib.import_module(module_path)

        if hasattr(module, "unpatch"):
            module.unpatch()
    except ImportError:
        pass  # If not imported, nothing to unpatch


def get_available_integrations() -> list:
    """Return a list of available integration names."""
    return sorted(_INTEGRATIONS.keys())


__all__ = [
    "patch",
    "unpatch",
    "get_available_integrations",
    "__version__",
]
