import functools
import os

from ddtrace.internal.schema import SCHEMA_VERSION
from ddtrace.internal.utils.formats import asbool


@functools.lru_cache(maxsize=1)
def _is_enabled():
    env_enabled = asbool(os.getenv("DD_TRACE_PEER_SERVICE_DEFAULTS_ENABLED", default=False))

    return SCHEMA_VERSION == "v1" or (SCHEMA_VERSION == "v0" and env_enabled)


PEER_SERVICE_ENABLED = _is_enabled()
