from ddtrace import config

from .session import patch
from .session import unpatch


__all__ = ["patch", "unpatch"]

config._add(
    "cassandra",
    dict(_default_service="cassandra"),
)
