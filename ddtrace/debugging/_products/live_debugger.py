from ddtrace.settings.live_debugging import config


# TODO[gab]: Uncomment when the product is ready
# requires = ["tracer"]


def post_preload():
    pass


def start() -> None:
    if config.enabled:
        from ddtrace.debugging._live import enable

        enable()


def restart(join: bool = False) -> None:
    pass


def stop(join: bool = False):
    if config.enabled:
        from ddtrace.debugging._live import disable

        disable()


def at_exit(join: bool = False):
    stop(join=join)
