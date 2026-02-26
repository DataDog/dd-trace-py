from ddtrace.internal.settings.live_debugging import config


# TODO[gab]: Uncomment when the product is ready
# requires = ["tracer"]


def post_preload() -> None:
    pass


def enabled() -> bool:
    return config.enabled


def start() -> None:
    from ddtrace.debugging._live import enable

    enable()


def restart(join: bool = False) -> None:
    pass


def stop(join: bool = False) -> None:
    from ddtrace.debugging._live import disable

    disable()


def at_exit(join: bool = False) -> None:
    stop(join=join)
