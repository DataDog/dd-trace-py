from collections.abc import Callable
from collections.abc import Mapping
from dataclasses import InitVar
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Optional
from typing import Union

from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.ext import db
from ddtrace.ext import net
from ddtrace.internal import core
from ddtrace.internal.core.events import Event
from ddtrace.internal.core.events import event_field
from ddtrace.internal.settings.integration import IntegrationConfig


_CONNECTION_FIELDS = {
    db.SYSTEM: "db_system",
    db.NAME: "db_name",
    db.USER: "db_user",
    net.TARGET_HOST: "target_host",
    net.TARGET_PORT: "target_port",
    net.SERVER_ADDRESS: "server_address",
}


@dataclass
class DatabaseOperationEvent(Event):
    """A SQL query, connection, or transaction operation shared by database drivers."""

    event_name = "database.operation"
    span_kind = SpanKind.CLIENT

    component: str = event_field()
    integration_config: IntegrationConfig = event_field()
    operation_name: str = event_field()
    service: Optional[str] = event_field(default=None)
    resource: Any = event_field(default=None)
    measured: bool = event_field(default=True)
    tags: dict[str, Any] = event_field(default_factory=dict)
    connection_tags: InitVar[Optional[Mapping[str, Any]]] = event_field(default=None)
    span_type: Optional[str] = event_field(default=SpanTypes.SQL)
    # Some legacy drivers attribute the service after starting the span.
    service_from_config: bool = event_field(default=False)
    metadata_before_tags: bool = event_field(default=False)
    db_system: Any = event_field(default=None)
    db_name: Any = event_field(default=None)
    db_user: Any = event_field(default=None)
    target_host: Any = event_field(default=None)
    target_port: Any = event_field(default=None)
    server_address: Any = event_field(default=None)
    connection_tags_updated: bool = event_field(default=False)
    _connection_tag_names: set[str] = event_field(default_factory=set)

    def __post_init__(self, connection_tags: Optional[Mapping[str, Any]]) -> None:
        tags = self.tags
        self.tags = {}
        self.set_connection_tags(connection_tags or {})
        self.tags.update(tags)
        self.connection_tags_updated = False

    def set_connection_tags(self, tags: Mapping[str, Any]) -> None:
        for key, value in tags.items():
            if key in _CONNECTION_FIELDS:
                setattr(self, _CONNECTION_FIELDS[key], value)
                self._connection_tag_names.add(key)
            else:
                self.tags[key] = value
        self.connection_tags_updated = True

    def get_connection_tags(self) -> dict[str, Any]:
        return {
            key: value
            for key, attr in _CONNECTION_FIELDS.items()
            if (value := getattr(self, attr)) is not None or key in self._connection_tag_names
        }


@dataclass
class DatabaseResultEvent(Event):
    """Cursor metadata published while its operation span is still active."""

    event_name = "database.result"

    context: core.ExecutionContext[DatabaseOperationEvent]
    row_count: Any = None
    row_number: Any = None
    row_count_as_tag: bool = False
    skip_none_row_count: bool = True
    tags: dict[str, Any] = field(default_factory=dict)
    # Compatibility bridge for existing cursor subclasses; new hooks publish metadata instead.
    legacy_post_execute: Optional[Callable[[Any], None]] = None


@dataclass
class DatabaseQueryPrepareEvent(Event):
    """Optional query preparation after operation metadata is available."""

    event_name = "database.query.prepare"

    context: core.ExecutionContext[DatabaseOperationEvent]
    args: tuple[Any, ...]
    kwargs: dict[str, Any]
    supports_query_rewriting: bool = True


@dataclass
class DbQueryEvent(Event):
    """A database query shared by instrumentation and product subscribers."""

    event_name = "db.query"

    query: Union[str, bytes, bytearray, memoryview]
    span_name_prefix: str
    # Returns the connection encoding of byte queries. It reads the live connection, so subscribers call it during
    # dispatch, and only when they need it since the lookup has a cost and can fail.
    get_encoding: Optional[Callable[[], Optional[str]]] = None
