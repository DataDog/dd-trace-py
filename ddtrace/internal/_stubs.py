"""
Centralized stubs for third-party modules when instrumentation is disabled.
This prevents imports that would load threading or other heavy dependencies.
"""

from typing import List

from ._instrumentation_enabled import _INSTRUMENTATION_ENABLED


if _INSTRUMENTATION_ENABLED:
    import logging

    import wrapt
else:
    # Provide minimal stubs when instrumentation is disabled

    class logging:  # type: ignore[no-redef]
        DEBUG = 10
        INFO = 20
        WARNING = 30
        ERROR = 40
        CRITICAL = 50

        class Logger:
            def debug(self, *args, **kwargs):
                pass

            def info(self, *args, **kwargs):
                pass

            def warning(self, *args, **kwargs):
                pass

            def error(self, *args, **kwargs):
                pass

            def critical(self, *args, **kwargs):
                pass

            def setLevel(self, level):
                pass

            def addHandler(self, handler):
                pass

            def addFilter(self, _filter):
                pass

            def isEnabledFor(self, level):
                return False

            handlers: List = []

        class LogRecord:
            def __init__(self, *args, **kwargs):
                self.name = ""
                self.msg = ""
                self.pathname = ""
                self.lineno = 0
                self.levelno = 0

        @staticmethod
        def getLogger(name):
            return logging.Logger()

        class StreamHandler:
            def __init__(self):
                pass

            def setLevel(self, level):
                pass

            def setFormatter(self, formatter):
                pass

            def set_name(self, name):
                pass

        class Formatter:
            def __init__(self, fmt):
                pass

        @staticmethod
        def warning(msg, *args):
            pass

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


# Export the stubs
__all__ = ["logging", "wrapt"]
