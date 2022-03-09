from ddtrace import config
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


config._add(
    "aiohttp",
    dict(
        distributed_tracing=True,
    ),
)


def patch():
    from ddtrace.contrib.aiohttp_jinja2 import patch as aiohttp_jinja2_patch

    aiohttp_jinja2_patch()


def unpatch():
    from ddtrace.contrib.aiohttp_jinja2 import unpatch as aiohttp_jinja2_unpatch

    aiohttp_jinja2_unpatch()
