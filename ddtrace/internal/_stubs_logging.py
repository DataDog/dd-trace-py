"""
Logging stubs that don't have circular import dependencies.
These are the logging-related stubs needed when instrumentation is disabled.
"""

from typing import List

from ._instrumentation_enabled import _INSTRUMENTATION_ENABLED


if _INSTRUMENTATION_ENABLED:
    import logging
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


# Export the logging stubs
__all__ = ["logging"]
