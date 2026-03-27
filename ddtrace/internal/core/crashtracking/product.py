from ddtrace.internal.settings.crashtracker import config


def post_preload() -> None:
    pass


def enabled() -> bool:
    return config.enabled


def start() -> None:
    from ddtrace.internal.core import crashtracking

    crashtracking.start()


def restart(join: bool = False) -> None:
    from ddtrace.internal.core import crashtracking

    crashtracking.restart()


def stop(join: bool = False) -> None:
    pass  # No explicit stop for crashtracking
