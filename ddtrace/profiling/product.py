from ddtrace.settings.profiling import config


def post_preload():
    pass


def start():
    if config.enabled:
        import ddtrace.profiling.auto  # noqa: F401


def restart(join=False):
    if config.enabled:
        from ddtrace.profiling import bootstrap

        try:
            bootstrap.profiler._restart_on_fork()
        except AttributeError:
            pass


def stop(join=False):
    if config.enabled:
        from ddtrace.profiling import bootstrap

        try:
            bootstrap.profiler.stop()
        except AttributeError:
            pass


def at_exit(join=False):
    stop(join=join)
