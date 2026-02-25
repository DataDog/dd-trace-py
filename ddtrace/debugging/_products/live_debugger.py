from ddtrace.internal.settings.live_debugging import config


# TODO[gab]: Uncomment when the product is ready
# requires = ["tracer"]


def post_preload() -> None:
    pass


def start() -> None:
    if config.enabled:
        from ddtrace.debugging._live import enable

        enable()


def restart(join: bool = False) -> None:
    pass


def stop(join: bool = False) -> None:
    if config.enabled:
        from ddtrace.debugging._live import disable

        disable()


def at_exit(join: bool = False) -> None:
    stop(join=join)
