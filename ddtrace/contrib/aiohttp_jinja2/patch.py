from ddtrace.vendor.debtcollector import deprecate

from ..internal.aiohttp_jinja2.patch_module import *  # noqa: F401,F403


deprecate(
    "This module is deprecated and will be removed in a future version. Avoid importing from this module.",
    version="3.0.0",
)
