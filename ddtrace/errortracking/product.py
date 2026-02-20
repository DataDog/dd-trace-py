"""
This is the entry point for the Error Tracking automatic reporting of handled exception.
"""

from ddtrace.internal.settings.errortracking import config


requires = ["tracer"]


def post_preload():
    pass


def enabled() -> bool:
    return config.enabled


def start() -> None:
    from ddtrace.errortracking._handled_exceptions.collector import HandledExceptionCollector

    HandledExceptionCollector.enable()


def restart(join: bool = False) -> None:
    pass


def stop(join: bool = False):
    from ddtrace.errortracking._handled_exceptions.collector import HandledExceptionCollector

    HandledExceptionCollector.disable()


def at_exit(join: bool = False):
    stop(join=join)
