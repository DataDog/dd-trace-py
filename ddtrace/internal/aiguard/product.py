from ddtrace.internal.settings.aiguard import aiguard_config


requires = []


def post_preload():
    pass


def enabled():
    return aiguard_config._ai_guard_enabled


def start():
    if aiguard_config._ai_guard_enabled:
        from ddtrace.aiguard._initialization import load_ai_guard

        load_ai_guard()


def restart(join=False):
    pass


def stop(join=False):
    pass
