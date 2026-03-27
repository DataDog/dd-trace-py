requires = ["tracer"]


def post_preload() -> None:
    pass


def enabled() -> bool:
    from ddtrace import config

    return config._runtime_metrics_enabled


def start() -> None:
    from ddtrace.internal.runtime.runtime_metrics import RuntimeWorker

    RuntimeWorker.enable()


def restart(join: bool = False) -> None:
    from ddtrace.internal.runtime.runtime_metrics import RuntimeWorker

    RuntimeWorker.disable()
    RuntimeWorker.enable()


def stop(join: bool = False) -> None:
    from ddtrace.internal.runtime.runtime_metrics import RuntimeWorker

    RuntimeWorker.disable()
