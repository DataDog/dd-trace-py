from dataclasses import dataclass
from enum import Enum
from typing import Any
from typing import ClassVar
from typing import Optional
from typing import Protocol

from ddtrace._trace.events import TracingEvent
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.ext import redis as redisx
from ddtrace.ext import valkey as valkeyx
from ddtrace.internal.core.events import event_field
from ddtrace.internal.schema import schematize_cache_operation


class CacheEvents(Enum):
    CACHE_COMMAND = "cache.command"


class CacheConnectionPool(Protocol):
    connection_kwargs: dict[str, Any]


class CacheConnectionProvider(Protocol):
    connection_pool: CacheConnectionPool


@dataclass
class CacheCommandEvent(TracingEvent):
    event_name = CacheEvents.CACHE_COMMAND.value

    span_kind = SpanKind.CLIENT
    span_type = SpanTypes.CACHE

    # Redis and Valkey expose equivalent command metadata, but each integration
    # uses its own tag constant names, so integration specific events override these
    # fields.
    raw_command_tag_name: ClassVar[Optional[str]] = None
    args_len_tag_name: ClassVar[Optional[str]] = None
    pipeline_len_tag_name: ClassVar[Optional[str]] = None
    db_tag_name: ClassVar[Optional[str]] = None
    client_name_tag_name: ClassVar[Optional[str]] = None

    # used to define operation_name
    cache_provider: str = event_field()
    command_tag_name: str = event_field()

    db_system: str = event_field()

    query: Optional[str] = event_field(default=None)
    args_len: Optional[int] = event_field(default=None)
    pipeline_len: Optional[int] = event_field(default=None)
    rowcount: Optional[int] = event_field(default=None)
    connection_tags: dict[str, Any] = event_field(default_factory=dict)
    connection_provider: Optional[CacheConnectionProvider] = event_field(default=None)

    def __post_init__(self) -> None:
        self.operation_name = schematize_cache_operation(self.command_tag_name, cache_provider=self.cache_provider)


# Events below are not redefining event_name on purpose.
# They are specialization of the CacheCommandEvent but not
# new events by themselves
class RedisCommandEvent(CacheCommandEvent):
    span_type = SpanTypes.REDIS

    raw_command_tag_name = redisx.RAWCMD
    args_len_tag_name = redisx.ARGS_LEN
    pipeline_len_tag_name = redisx.PIPELINE_LEN
    db_tag_name = redisx.DB
    client_name_tag_name = redisx.CLIENT_NAME


class ValkeyCommandEvent(CacheCommandEvent):
    span_type = SpanTypes.VALKEY

    raw_command_tag_name = valkeyx.RAWCMD
    args_len_tag_name = valkeyx.ARGS_LEN
    pipeline_len_tag_name = valkeyx.PIPELINE_LEN
    db_tag_name = valkeyx.DB
    client_name_tag_name = valkeyx.CLIENT_NAME
