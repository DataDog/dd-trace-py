from ddtrace.internal.settings.aiguard import aiguard_config


requires: list[str] = []


def post_preload() -> None:
    pass


def enabled() -> bool:
    return bool(aiguard_config._ai_guard_enabled)


def start() -> None:
    from ddtrace.aiguard._initialization import load_ai_guard

    load_ai_guard()


def restart(join: bool = False) -> None:
    pass


def stop(join: bool = False) -> None:
    pass
