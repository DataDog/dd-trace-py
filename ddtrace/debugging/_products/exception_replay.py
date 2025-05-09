from ddtrace.debugging._config import er_config as config


# TODO[gab]: Uncomment this when the feature is ready
# requires = ["tracer"]


def post_preload():
    pass


def start():
    if config.enabled:
        from ddtrace.debugging._exception.replay import SpanExceptionHandler

        SpanExceptionHandler.enable()


def restart(join=False):
    pass


def stop(join=False):
    if config.enabled:
        from ddtrace.debugging._exception.replay import SpanExceptionHandler

        SpanExceptionHandler.disable()


def at_exit(join=False):
    stop(join=join)
