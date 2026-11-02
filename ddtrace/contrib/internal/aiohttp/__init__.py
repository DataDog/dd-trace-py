from ddtrace.internal.settings._config import config
from ddtrace.internal.telemetry import get_config as _get_config
from ddtrace.internal.utils.formats import asbool


config._add(  # type: ignore[no-untyped-call]
    "aiohttp",
    dict(
        distributed_tracing=True,
        disable_stream_timing_for_mem_leak=asbool(
            _get_config("DD_AIOHTTP_CLIENT_DISABLE_STREAM_TIMING_FOR_MEM_LEAK", default=False)
        ),
    ),
)
