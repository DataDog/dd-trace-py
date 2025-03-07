"""
This is the entry point for the Error Tracking automatic reporting of handled exception.
"""
from ddtrace.settings.errortracking import config


# TODO[dubloom]: Uncomment when the product is ready
# requires = ["tracer"]


def post_preload():
    pass


def start() -> None:
    if config.enabled:
        from ddtrace.errortracking._handled_exceptions.collector import HandledExceptionCollector

        HandledExceptionCollector.enable()


def restart(join: bool = False) -> None:
    pass


def stop(join: bool = False):
    if config.enabled:
        from ddtrace.errortracking._handled_exceptions.collector import HandledExceptionCollector

        HandledExceptionCollector.disable()


def at_exit(join: bool = False):
    stop(join=join)
