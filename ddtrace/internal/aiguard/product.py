from ddtrace.internal.settings.aiguard import aiguard_config as config


requires: list[str] = []


def post_preload() -> None:
    pass


def enabled() -> bool:
    return bool(config._ai_guard_enabled)


def start() -> None:
    from ddtrace.aiguard._initialization import load_ai_guard

    load_ai_guard()


def restart(join: bool = False) -> None:
    pass


def stop(join: bool = False) -> None:
    pass
