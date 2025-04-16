__all__ = [
    "patch",
    "patch_all",
    "config",
]


def __getattr__(name):
    IMPORTS = {
        "patch": "ddtrace._monkey",
        "patch_all": "ddtrace._monkey",
    }

    if name in IMPORTS:
        import importlib

        attr = getattr(importlib.import_module(IMPORTS[name]), name)

    elif name == "__version__":
        from ddtrace.version import get_version

        attr = get_version()

    elif name == "config":
        from ddtrace.settings._config import Config as _Config

        attr = _Config()

    elif name == "tracer":
        from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
        from ddtrace.trace import tracer
        import ddtrace.vendor.debtcollector as debtcollector

        msg = (
            "Accessing `ddtrace.tracer` is deprecated and will be removed in version 4. "
            "Use `ddtrace.trace.tracer` instead."
        )
        debtcollector.deprecate(msg, category=DDTraceDeprecationWarning)

        attr = tracer

    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    globals()[name] = attr

    return attr
