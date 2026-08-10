from ddtrace.internal.settings.live_debugging import config


# TODO[gab]: Uncomment when the product is ready
# requires = ["tracer"]


def post_preload() -> None:
    pass


def enabled() -> bool:
    # TODO: remove bool() cast once envier mypy plugin resolves config
    # attributes to their declared types
    return bool(config.enabled)


def start() -> None:
    from ddtrace.debugging._live import enable

    enable()


def restart(join: bool = False) -> None:
    pass


def stop(join: bool = False) -> None:
    from ddtrace.debugging._live import disable

    disable()
