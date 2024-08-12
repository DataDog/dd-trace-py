from ddtrace.debugging._config import er_config


def post_preload():
    pass


def start():
    if er_config.enabled:
        from ddtrace.debugging._exception.replay import SpanExceptionProcessor

        SpanExceptionProcessor().register()


def restart(join=False):
    pass


def stop(join=False):
    # TODO: Currently unable to disable the exception replay feature
    pass


def at_exit(join=False):
    stop(join=join)
