"""Product plugin that lets the profiler react to APM_TRACING remote config payloads."""

import typing as t

from ddtrace.internal.settings.profiling import config as profiling_config


requires: list[str] = ["apm-tracing-rc"]

last_lib_config: t.Optional[dict[str, t.Any]] = None


def post_preload() -> None:
    pass


def enabled() -> bool:
    return profiling_config.enabled


def start() -> None:
    pass


def restart(join: bool = False) -> None:
    pass


def stop(join: bool = False) -> None:
    pass


def apm_tracing_rc(lib_config: dict[str, t.Any], dd_config: t.Any) -> None:
    global last_lib_config
    last_lib_config = lib_config
    print(f"[profiling] APM_TRACING RC lib_config: {lib_config}")
