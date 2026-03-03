from ddtrace.internal.settings.symbol_db import config


requires = ["remote-configuration"]


def post_preload() -> None:
    pass


def start() -> None:
    if config.enabled:
        from ddtrace.internal import symbol_db

        symbol_db.bootstrap()


def restart(join: bool = False) -> None:
    if not config._force:
        from ddtrace.internal import symbol_db

        symbol_db.restart()


def stop(join: bool = False) -> None:
    # Controlled via RC
    pass


def at_exit(join: bool = False) -> None:
    stop(join=join)
