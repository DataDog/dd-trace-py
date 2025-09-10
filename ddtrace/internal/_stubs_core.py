"""
Core stubs that don't have circular import dependencies.
These are the wrapt-related stubs needed early in the import process.
"""

from ._instrumentation_enabled import _INSTRUMENTATION_ENABLED


if _INSTRUMENTATION_ENABLED:
    import wrapt
    from wrapt.importer import when_imported
else:
    # Provide minimal stubs when instrumentation is disabled

    class wrapt:  # type: ignore[no-redef]
        class ObjectProxy:
            def __init__(self, wrapped):
                self._self_wrapped = wrapped

            def __getattr__(self, name):
                return getattr(self._self_wrapped, name)

        @staticmethod
        def decorator(wrapper):
            def _decorator(wrapped):
                return wrapped

            return _decorator

        class importer:
            @staticmethod
            def when_imported(name):
                def decorator(func):
                    return func

                return decorator

    def when_imported(x):
        return lambda y: None


# Export the core stubs
__all__ = ["wrapt", "when_imported"]
