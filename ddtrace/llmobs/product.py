requires = ["tracer"]


def post_preload() -> None:
    pass


def enabled() -> bool:
    from ddtrace import config

    return config._llmobs_enabled


def start() -> None:
    from ddtrace.llmobs import LLMObs

    LLMObs.enable(_auto=True)


def restart(join: bool = False) -> None:
    from ddtrace.llmobs import LLMObs

    if LLMObs._instance is not None:
        LLMObs._instance._child_after_fork()


def stop(join: bool = False) -> None:
    from ddtrace.llmobs import LLMObs

    LLMObs.disable()
