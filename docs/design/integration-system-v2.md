# Integration System v2 - Design Document

## Overview

A clean integration architecture for dd-trace-py modeled after dd-trace-js:
1. **Publisher Side** (Patch): Integration code emits events as `{pkg_name}.{func_name}`
2. **Subscriber Side** (Trace): Hierarchical plugin system with category-based base classes

**Directory Structure:**
- `ddtrace/contrib/auto/` - New clean integrations (publisher side)
- `ddtrace/_trace/tracing_plugins/base/` - Base plugin classes
- `ddtrace/_trace/tracing_plugins/contrib/` - Integration-specific plugins

---

## Plugin Hierarchy

```
TracingPlugin (base)
│
├── OutboundPlugin (connections TO external services)
│   │   - peer service resolution
│   │   - host/port tagging
│   │   - span finishing with peer service tags
│   │
│   ├── ClientPlugin (kind: CLIENT)
│   │   │   - type: "web" (default)
│   │   │
│   │   └── StoragePlugin (type: "storage")
│   │       │   - system-based service naming
│   │       │
│   │       └── DatabasePlugin
│   │               - DBM propagation
│   │               - query tagging
│   │               - rowcount tracking
│   │
│   └── ProducerPlugin (kind: PRODUCER)
│           - type: "worker"
│           - trace context injection
│           - destination tagging
│
└── InboundPlugin (connections FROM external sources)
    │   - distributed context extraction
    │   - parent store binding
    │
    ├── ServerPlugin (kind: SERVER)
    │   │   - type: "web"
    │   │
    │   └── RouterPlugin
    │           - route tagging
    │           - path parameter extraction
    │
    └── ConsumerPlugin (kind: CONSUMER)
            - type: "worker"
            - trace context extraction
            - message metadata tagging
```

---

## Part 1: Directory Structure

```
ddtrace/
├── contrib/
│   ├── internal/           # Existing integrations (legacy)
│   │   └── ...
│   │
│   └── auto/               # NEW: Clean integrations (publisher side)
│       ├── __init__.py
│       ├── asyncpg/
│       │   ├── __init__.py
│       │   └── patch.py    # Emits asyncpg.execute, asyncpg.connect
│       ├── psycopg/
│       │   └── patch.py    # Emits psycopg.execute
│       ├── httpx/
│       │   └── patch.py    # Emits httpx.send
│       ├── flask/
│       │   └── patch.py    # Emits flask.request
│       └── kafka/
│           └── patch.py    # Emits kafka.produce, kafka.consume
│
└── _trace/
    └── tracing_plugins/
        ├── __init__.py         # Plugin registration
        │
        ├── base/               # Base plugin classes
        │   ├── __init__.py
        │   ├── tracing.py      # TracingPlugin (root base class)
        │   ├── outbound.py     # OutboundPlugin
        │   ├── client.py       # ClientPlugin
        │   ├── storage.py      # StoragePlugin
        │   ├── database.py     # DatabasePlugin
        │   ├── producer.py     # ProducerPlugin (extends Outbound)
        │   ├── inbound.py      # InboundPlugin
        │   ├── server.py       # ServerPlugin
        │   ├── router.py       # RouterPlugin
        │   ├── consumer.py     # ConsumerPlugin (extends Inbound)
        │   └── events.py       # Event context types
        │
        └── contrib/            # Integration-specific plugins
            ├── __init__.py
            ├── asyncpg.py      # AsyncpgPlugin(DatabasePlugin)
            ├── psycopg.py      # PsycopgPlugin(DatabasePlugin)
            ├── mysql.py        # MySQLPlugin(DatabasePlugin)
            ├── httpx.py        # HttpxPlugin(ClientPlugin)
            ├── aiohttp.py      # AiohttpPlugin(ClientPlugin)
            ├── flask.py        # FlaskPlugin(RouterPlugin)
            ├── django.py       # DjangoPlugin(RouterPlugin)
            └── kafka.py        # KafkaProducerPlugin, KafkaConsumerPlugin
```

---

## Part 2: Base Plugin Classes

### Location: `ddtrace/_trace/tracing_plugins/base/`

### `tracing.py` - Root Base Class

```python
"""
Base tracing plugin class.

All plugins inherit from this. Provides core subscription and span lifecycle.
"""
from abc import ABC, abstractmethod
from typing import Any, Tuple, Optional
from ddtrace.internal import core


class TracingPlugin(ABC):
    """
    Root base class for all tracing plugins.

    Subclasses define `package` and `operation` to auto-subscribe to events.
    The event pattern is: context.started.{package}.{operation}
    """

    # --- Required attributes (override in subclasses) ---

    @property
    @abstractmethod
    def package(self) -> str:
        """Package name (e.g., 'asyncpg', 'flask')."""
        pass

    @property
    @abstractmethod
    def operation(self) -> str:
        """Operation name (e.g., 'execute', 'request')."""
        pass

    # --- Optional attributes (override as needed) ---

    kind: Optional[str] = None  # SpanKind: "client", "server", "producer", "consumer"
    type: str = "custom"  # Span type category: "web", "sql", "storage", etc.

    # --- Computed properties ---

    @property
    def event_name(self) -> str:
        """Full event name: {package}.{operation}"""
        return f"{self.package}.{self.operation}"

    @property
    def integration_config(self):
        """Get the integration's config."""
        from ddtrace import config
        return config.get(self.package, {})

    # --- Registration ---

    def register(self) -> None:
        """Register this plugin's event handlers."""
        core.on(f"context.started.{self.event_name}", self._on_started)
        core.on(f"context.ended.{self.event_name}", self._on_ended)

    # --- Event handlers ---

    def _on_started(self, ctx: core.ExecutionContext) -> None:
        """Internal: called when context starts."""
        self.on_start(ctx)

    def _on_ended(self, ctx: core.ExecutionContext, exc_info: Tuple) -> None:
        """Internal: called when context ends."""
        self.on_finish(ctx, exc_info)

    # --- Override these in subclasses ---

    def on_start(self, ctx: core.ExecutionContext) -> None:
        """Called when the traced context starts. Creates span."""
        pass

    def on_finish(self, ctx: core.ExecutionContext, exc_info: Tuple) -> None:
        """Called when the traced context ends. Finishes span."""
        span = ctx.span
        if not span:
            return

        if exc_info[0]:
            span.set_exc_info(*exc_info)

        span.finish()

    # --- Utility methods ---

    def start_span(self, ctx: core.ExecutionContext, name: str, **options) -> Any:
        """Create and configure a span."""
        pin = ctx.get_item("pin")
        if not pin or not pin.enabled():
            return None

        from ddtrace.constants import SPAN_KIND
        from ddtrace.internal.constants import COMPONENT

        span = pin.tracer.trace(name, **options)

        # Set common tags
        span._set_tag_str(COMPONENT, self.integration_config.get("integration_name", self.package))
        if self.kind:
            span._set_tag_str(SPAN_KIND, self.kind)

        ctx.span = span
        return span
```

### `outbound.py` - Outbound Connections

```python
"""
OutboundPlugin - Base for all outgoing connections to external services.

Handles:
- Peer service resolution
- Host/port tagging
- Span finishing with peer service tags
"""
from typing import Optional, Tuple
from ddtrace.internal import core
from ddtrace.ext import net
from ddtrace._trace.tracing_plugins.base.tracing import TracingPlugin


class OutboundPlugin(TracingPlugin):
    """
    Base plugin for outbound connections (DB, HTTP client, messaging produce).

    Provides peer service resolution and host tagging.
    """

    type = "outbound"

    def on_finish(self, ctx: core.ExecutionContext, exc_info: Tuple) -> None:
        """Finish span with peer service tagging."""
        span = ctx.span
        if not span:
            return

        # Set peer service if not already set
        self._set_peer_service(ctx, span)

        # Call parent to handle error and finish
        super().on_finish(ctx, exc_info)

    def _set_peer_service(self, ctx: core.ExecutionContext, span) -> None:
        """Determine and set peer service tag."""
        from ddtrace import config

        # Skip if already set
        if span.get_tag("peer.service"):
            return

        # Try to derive from standard tags
        peer_service = self._get_peer_service(ctx, span)
        if peer_service:
            span._set_tag_str("peer.service", peer_service)

    def _get_peer_service(self, ctx: core.ExecutionContext, span) -> Optional[str]:
        """
        Get peer service from available tags.

        Priority:
        1. Explicit peer.service tag
        2. net.peer.name
        3. out.host / net.target.host
        4. Service-specific derivation
        """
        # Check standard network tags
        for tag in ("net.peer.name", "out.host", net.TARGET_HOST):
            value = span.get_tag(tag)
            if value:
                return value

        # Let subclasses override
        return None

    def add_host(self, span, host: Optional[str], port: Optional[int] = None) -> None:
        """Tag span with host and port info."""
        if host:
            span._set_tag_str(net.TARGET_HOST, host)
            span._set_tag_str(net.SERVER_ADDRESS, host)
        if port:
            span.set_metric(net.TARGET_PORT, port)
```

### `client.py` - Client Operations

```python
"""
ClientPlugin - Base for client-side operations (HTTP clients, etc.)

Extends OutboundPlugin with client-specific behavior.
"""
from ddtrace.ext import SpanKind
from ddtrace._trace.tracing_plugins.base.outbound import OutboundPlugin


class ClientPlugin(OutboundPlugin):
    """
    Base plugin for client-side operations.

    - kind: CLIENT
    - type: "web" by default (override for storage)
    """

    kind = SpanKind.CLIENT
    type = "web"
```

### `storage.py` - Storage Systems

```python
"""
StoragePlugin - Base for storage system clients (databases, caches, etc.)

Extends ClientPlugin with storage-specific service naming.
"""
from typing import Any, Optional
from ddtrace.internal import core
from ddtrace._trace.tracing_plugins.base.client import ClientPlugin


class StoragePlugin(ClientPlugin):
    """
    Base plugin for storage systems (databases, caches).

    Provides system-based service naming: {tracer_service}-{system}
    """

    type = "storage"

    # Override in subclasses
    system: Optional[str] = None  # e.g., "postgresql", "redis", "mysql"

    def start_span(self, ctx: core.ExecutionContext, name: str, **options) -> Any:
        """Create span with system-based service naming."""
        from ddtrace.contrib.internal.trace_utils import ext_service

        pin = ctx.get_item("pin")

        # Default service: {base_service}-{system}
        if "service" not in options and self.system:
            options["service"] = ext_service(pin, self.integration_config)

        return super().start_span(ctx, name, **options)
```

### `database.py` - Database Operations

```python
"""
DatabasePlugin - Base for all database integrations.

Extends StoragePlugin with:
- DBM (Database Monitoring) propagation
- Query tagging and truncation
- Rowcount tracking
- Standard DB tags
"""
from typing import Optional, Tuple, Any
from ddtrace.internal import core
from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.ext import SpanTypes, db, net
from ddtrace._trace.tracing_plugins.base.storage import StoragePlugin


class DatabasePlugin(StoragePlugin):
    """
    Base plugin for database integrations.

    Handles all common database tracing:
    - Standard tags (db.system, db.name, db.user, etc.)
    - DBM comment propagation
    - Query resource tagging
    - Rowcount tracking
    """

    type = "sql"

    # Override in subclasses
    db_system: Optional[str] = None  # "postgresql", "mysql", "sqlite"

    def on_start(self, ctx: core.ExecutionContext) -> None:
        """Create database span with standard tags."""
        pin = ctx.get_item("pin")
        db_ctx = ctx.get_item("db_context")

        if not pin or not pin.enabled():
            return

        # Get span name
        span_name = ctx.get_item("span_name") or self._get_span_name()

        # Create span
        span = self.start_span(
            ctx,
            span_name,
            resource=ctx.get_item("resource") or (db_ctx.query if db_ctx else None),
            span_type=SpanTypes.SQL,
        )

        if not span:
            return

        # Mark as measured
        span.set_metric(_SPAN_MEASURED_KEY, 1)

        # Set database tags
        if db_ctx:
            self._set_database_tags(span, db_ctx)

        # Pin tags
        if pin.tags:
            span.set_tags(pin.tags)

        # DBM propagation
        if db_ctx and db_ctx.dbm_propagator:
            self._apply_dbm_propagation(ctx, span, db_ctx)

    def on_finish(self, ctx: core.ExecutionContext, exc_info: Tuple) -> None:
        """Finish span with rowcount."""
        span = ctx.span
        if not span:
            return

        # Set rowcount if available
        rowcount = ctx.get_item("rowcount")
        if rowcount is not None:
            span.set_metric(db.ROWCOUNT, rowcount)
            if isinstance(rowcount, int) and rowcount >= 0:
                span.set_tag(db.ROWCOUNT, rowcount)

        super().on_finish(ctx, exc_info)

    def _get_span_name(self) -> str:
        """Get default span name for this database."""
        from ddtrace.internal.schema import schematize_database_operation

        system = self.db_system or self.system or "db"
        return schematize_database_operation(
            f"{system}.query",
            database_provider=system
        )

    def _set_database_tags(self, span, db_ctx) -> None:
        """Set standard database tags."""
        if db_ctx.db_system:
            span._set_tag_str(db.SYSTEM, db_ctx.db_system)

        if db_ctx.host:
            self.add_host(span, db_ctx.host, db_ctx.port)

        if db_ctx.user:
            span._set_tag_str(db.USER, db_ctx.user)

        if db_ctx.database:
            span._set_tag_str(db.NAME, db_ctx.database)

        # Extra tags from context
        if db_ctx.tags:
            span.set_tags(db_ctx.tags)

    def _apply_dbm_propagation(self, ctx, span, db_ctx) -> None:
        """Apply DBM comment injection if configured."""
        if not db_ctx.dbm_propagator:
            return

        # Dispatch to DBM handler
        result = core.dispatch_with_results(
            f"{self.package}.execute",
            (self.integration_config, span, ctx.get_item("args", ()), ctx.get_item("kwargs", {}))
        ).result

        if result:
            _, args, kwargs = result.value
            ctx.set_item("modified_args", args)
            ctx.set_item("modified_kwargs", kwargs)
```

### `producer.py` - Message Producers (extends Outbound)

```python
"""
ProducerPlugin - Base for message producers.

Extends OutboundPlugin since producing messages is an outbound operation.
"""
from typing import Tuple
from ddtrace.internal import core
from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.ext import SpanKind, SpanTypes
from ddtrace._trace.tracing_plugins.base.outbound import OutboundPlugin


class ProducerPlugin(OutboundPlugin):
    """
    Base plugin for message producers.

    Extends OutboundPlugin - producing is sending data OUT.

    Handles:
    - Trace context injection into message headers
    - Destination tagging
    - Message metadata
    """

    kind = SpanKind.PRODUCER
    type = "worker"

    def on_start(self, ctx: core.ExecutionContext) -> None:
        """Create producer span with context injection."""
        from ddtrace.propagation.http import HTTPPropagator

        pin = ctx.get_item("pin")
        msg_ctx = ctx.get_item("messaging_context")

        if not pin or not pin.enabled() or not msg_ctx:
            return

        span_name = f"{msg_ctx.messaging_system}.produce"

        span = self.start_span(
            ctx,
            span_name,
            resource=msg_ctx.destination,
            span_type=SpanTypes.WORKER,
        )

        if not span:
            return

        span.set_metric(_SPAN_MEASURED_KEY, 1)
        span._set_tag_str("messaging.system", msg_ctx.messaging_system)
        span._set_tag_str("messaging.destination.name", msg_ctx.destination)

        # Add host if available (broker address)
        if hasattr(msg_ctx, 'host') and msg_ctx.host:
            self.add_host(span, msg_ctx.host, getattr(msg_ctx, 'port', None))

        # Inject trace context into message headers
        if msg_ctx.inject_headers:
            headers = {}
            HTTPPropagator.inject(span.context, headers)
            msg_ctx.inject_headers(headers)

    def on_finish(self, ctx: core.ExecutionContext, exc_info: Tuple) -> None:
        """Finish producer span with message metadata."""
        span = ctx.span
        if not span:
            return

        msg_ctx = ctx.get_item("messaging_context")

        if msg_ctx:
            if msg_ctx.message_id:
                span._set_tag_str("messaging.message.id", msg_ctx.message_id)
            if msg_ctx.partition is not None:
                span.set_metric("messaging.kafka.partition", msg_ctx.partition)

        super().on_finish(ctx, exc_info)
```

### `inbound.py` - Inbound Connections

```python
"""
InboundPlugin - Base for incoming requests.

Handles distributed context extraction.
"""
from typing import Tuple
from ddtrace.internal import core
from ddtrace._trace.tracing_plugins.base.tracing import TracingPlugin


class InboundPlugin(TracingPlugin):
    """
    Base plugin for inbound connections (web frameworks, message consumers).

    Handles distributed tracing context extraction.
    """

    type = "inbound"

    def bind_finish(self, ctx: core.ExecutionContext):
        """Return parent store for context binding."""
        return ctx.get_item("parent_store")
```

### `server.py` - Server Operations

```python
"""
ServerPlugin - Base for server-side request handling.
"""
from ddtrace.ext import SpanKind
from ddtrace._trace.tracing_plugins.base.inbound import InboundPlugin


class ServerPlugin(InboundPlugin):
    """
    Base plugin for server-side request handling.

    - kind: SERVER
    - type: "web"
    """

    kind = SpanKind.SERVER
    type = "web"
```

### `router.py` - Router Operations

```python
"""
RouterPlugin - Base for web framework routing.

Extends ServerPlugin with route tagging.
"""
from typing import Tuple
from ddtrace.internal import core
from ddtrace.ext import http
from ddtrace._trace.tracing_plugins.base.server import ServerPlugin


class RouterPlugin(ServerPlugin):
    """
    Base plugin for web framework routing.

    Handles:
    - Route template tagging
    - Path parameter extraction
    - Request/response meta tagging
    """

    def on_start(self, ctx: core.ExecutionContext) -> None:
        """Create request span with HTTP tags."""
        from ddtrace.constants import _SPAN_MEASURED_KEY
        from ddtrace.contrib.internal.trace_utils import set_http_meta

        pin = ctx.get_item("pin")
        web_ctx = ctx.get_item("web_context")

        if not pin or not pin.enabled():
            return

        span_name = ctx.get_item("span_name") or f"{self.package}.request"

        span = self.start_span(ctx, span_name)
        if not span:
            return

        span.set_metric(_SPAN_MEASURED_KEY, 1)

        if web_ctx:
            # Set resource as method + route
            route = web_ctx.route or web_ctx.path
            span.resource = f"{web_ctx.method} {route}"

            # Set HTTP meta
            set_http_meta(
                span,
                self.integration_config,
                method=web_ctx.method,
                url=web_ctx.url,
                route=web_ctx.route,
                request_headers=web_ctx.headers,
            )

    def on_finish(self, ctx: core.ExecutionContext, exc_info: Tuple) -> None:
        """Finish with response tags."""
        from ddtrace.contrib.internal.trace_utils import set_http_meta

        span = ctx.span
        if not span:
            return

        web_response = ctx.get_item("web_response")
        if web_response:
            set_http_meta(
                span,
                self.integration_config,
                status_code=web_response.status_code,
                response_headers=web_response.headers,
            )

        super().on_finish(ctx, exc_info)
```

### `consumer.py` - Message Consumers (extends Inbound)

```python
"""
ConsumerPlugin - Base for message consumers.

Extends InboundPlugin since consuming messages is an inbound operation.
"""
from typing import Tuple
from ddtrace.internal import core
from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.ext import SpanKind, SpanTypes
from ddtrace._trace.tracing_plugins.base.inbound import InboundPlugin


class ConsumerPlugin(InboundPlugin):
    """
    Base plugin for message consumers.

    Extends InboundPlugin - consuming is receiving data IN.

    Handles:
    - Trace context extraction from message headers
    - Span linking to producer
    - Message metadata tagging
    """

    kind = SpanKind.CONSUMER
    type = "worker"

    def on_start(self, ctx: core.ExecutionContext) -> None:
        """Create consumer span with context extraction."""
        from ddtrace.propagation.http import HTTPPropagator

        pin = ctx.get_item("pin")
        msg_ctx = ctx.get_item("messaging_context")

        if not pin or not pin.enabled() or not msg_ctx:
            return

        span_name = f"{msg_ctx.messaging_system}.consume"

        span = self.start_span(
            ctx,
            span_name,
            resource=msg_ctx.destination,
            span_type=SpanTypes.WORKER,
        )

        if not span:
            return

        span.set_metric(_SPAN_MEASURED_KEY, 1)
        span._set_tag_str("messaging.system", msg_ctx.messaging_system)
        span._set_tag_str("messaging.destination.name", msg_ctx.destination)

        # Extract and link trace context from message headers
        if msg_ctx.headers:
            distributed_ctx = HTTPPropagator.extract(msg_ctx.headers)
            if distributed_ctx:
                span.link_span(distributed_ctx)

    def on_finish(self, ctx: core.ExecutionContext, exc_info: Tuple) -> None:
        """Finish consumer span with message metadata."""
        span = ctx.span
        if not span:
            return

        msg_ctx = ctx.get_item("messaging_context")

        if msg_ctx:
            if msg_ctx.message_id:
                span._set_tag_str("messaging.message.id", msg_ctx.message_id)
            if msg_ctx.partition is not None:
                span.set_metric("messaging.kafka.partition", msg_ctx.partition)
            if msg_ctx.offset is not None:
                span.set_metric("messaging.kafka.offset", msg_ctx.offset)

        super().on_finish(ctx, exc_info)
```

---

## Part 3: Event Context Types

### Location: `ddtrace/_trace/tracing_plugins/base/events.py`

```python
"""
Event context types - the contract between patch and subscriber sides.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Callable


@dataclass
class DatabaseContext:
    """Context passed by database integrations."""
    db_system: str  # "postgresql", "mysql", "sqlite"
    query: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    user: Optional[str] = None
    database: Optional[str] = None
    dbm_propagator: Optional[Any] = None
    tags: Dict[str, Any] = field(default_factory=dict)


@dataclass
class HTTPClientContext:
    """Context passed by HTTP client integrations."""
    method: str
    url: str
    target_host: Optional[str] = None
    target_port: Optional[int] = None
    headers: Dict[str, str] = field(default_factory=dict)
    inject_headers: Optional[Callable[[Dict[str, str]], None]] = None
    tags: Dict[str, Any] = field(default_factory=dict)


@dataclass
class HTTPClientResponseContext:
    """Response context for HTTP clients."""
    status_code: int
    headers: Dict[str, str] = field(default_factory=dict)


@dataclass
class WebContext:
    """Context passed by web framework integrations."""
    method: str
    url: str
    path: str
    route: Optional[str] = None
    headers: Dict[str, str] = field(default_factory=dict)
    query_params: Dict[str, str] = field(default_factory=dict)
    path_params: Dict[str, str] = field(default_factory=dict)
    tags: Dict[str, Any] = field(default_factory=dict)


@dataclass
class WebResponseContext:
    """Response context for web frameworks."""
    status_code: int
    headers: Dict[str, str] = field(default_factory=dict)


@dataclass
class MessagingContext:
    """Context passed by messaging integrations."""
    messaging_system: str  # "kafka", "rabbitmq", "sqs"
    destination: str  # topic, queue name
    operation: str  # "produce", "consume"

    # Optional connection info (for peer service)
    host: Optional[str] = None
    port: Optional[int] = None

    # Message metadata
    message_id: Optional[str] = None
    partition: Optional[int] = None
    offset: Optional[int] = None

    # For distributed tracing
    headers: Dict[str, str] = field(default_factory=dict)
    inject_headers: Optional[Callable[[Dict[str, str]], None]] = None

    tags: Dict[str, Any] = field(default_factory=dict)
```

---

## Part 4: Integration-Specific Plugins

### Location: `ddtrace/_trace/tracing_plugins/contrib/`

### `asyncpg.py`

```python
"""asyncpg integration plugin."""
from ddtrace._trace.tracing_plugins.base.database import DatabasePlugin


class AsyncpgExecutePlugin(DatabasePlugin):
    """Handles asyncpg.execute events."""

    package = "asyncpg"
    operation = "execute"
    system = "postgresql"
    db_system = "postgresql"


class AsyncpgConnectPlugin(DatabasePlugin):
    """Handles asyncpg.connect events."""

    package = "asyncpg"
    operation = "connect"
    system = "postgresql"
    db_system = "postgresql"

    def _get_span_name(self) -> str:
        return "postgres.connect"
```

### `psycopg.py`

```python
"""psycopg integration plugin."""
from ddtrace._trace.tracing_plugins.base.database import DatabasePlugin


class PsycopgPlugin(DatabasePlugin):
    """Handles psycopg.execute events."""

    package = "psycopg"
    operation = "execute"
    system = "postgresql"
    db_system = "postgresql"

    def on_start(self, ctx) -> None:
        """Handle psycopg-specific SQL.Composable types."""
        super().on_start(ctx)

        # Convert Composable to string for resource
        db_ctx = ctx.get_item("db_context")
        if db_ctx and db_ctx.query and hasattr(db_ctx.query, "as_string"):
            if ctx.span:
                ctx.span.resource = str(db_ctx.query)
```

### `httpx.py`

```python
"""httpx integration plugin."""
from ddtrace._trace.tracing_plugins.base.client import ClientPlugin
from ddtrace.internal import core
from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.ext import SpanTypes
from ddtrace.propagation.http import HTTPPropagator
from ddtrace.contrib.internal.trace_utils import set_http_meta


class HttpxPlugin(ClientPlugin):
    """Handles httpx.send events."""

    package = "httpx"
    operation = "send"

    def on_start(self, ctx: core.ExecutionContext) -> None:
        """Create HTTP client span."""
        from ddtrace.internal.schema import schematize_url_operation
        from ddtrace.internal.schema.span_attribute_schema import SpanDirection

        pin = ctx.get_item("pin")
        http_ctx = ctx.get_item("http_context")

        if not pin or not pin.enabled():
            return

        span_name = schematize_url_operation(
            "http.request",
            protocol="http",
            direction=SpanDirection.OUTBOUND
        )

        service = self._get_service(pin, http_ctx)

        span = self.start_span(ctx, span_name, service=service, span_type=SpanTypes.HTTP)
        if not span:
            return

        span.set_metric(_SPAN_MEASURED_KEY, 1)

        # Add host tags for peer service
        if http_ctx:
            self.add_host(span, http_ctx.target_host, http_ctx.target_port)

        # Inject distributed tracing headers
        if self.integration_config.get("distributed_tracing", True) and http_ctx and http_ctx.inject_headers:
            headers = {}
            HTTPPropagator.inject(span.context, headers)
            http_ctx.inject_headers(headers)

    def on_finish(self, ctx, exc_info) -> None:
        """Finish with HTTP meta tags."""
        span = ctx.span
        if not span:
            return

        http_ctx = ctx.get_item("http_context")
        http_response = ctx.get_item("http_response")

        if http_ctx:
            set_http_meta(
                span,
                self.integration_config,
                method=http_ctx.method,
                url=http_ctx.url,
                target_host=http_ctx.target_host,
                status_code=http_response.status_code if http_response else None,
                request_headers=http_ctx.headers,
                response_headers=http_response.headers if http_response else None,
            )

        super().on_finish(ctx, exc_info)

    def _get_service(self, pin, http_ctx):
        """Determine service name."""
        from ddtrace.contrib.internal.trace_utils import ext_service

        if self.integration_config.get("split_by_domain") and http_ctx:
            service = http_ctx.target_host or ""
            if http_ctx.target_port:
                service += f":{http_ctx.target_port}"
            return service
        return ext_service(pin, self.integration_config)
```

### `flask.py`

```python
"""flask integration plugin."""
from ddtrace._trace.tracing_plugins.base.router import RouterPlugin


class FlaskPlugin(RouterPlugin):
    """Handles flask.request events."""

    package = "flask"
    operation = "request"
```

### `kafka.py`

```python
"""kafka integration plugins."""
from ddtrace._trace.tracing_plugins.base.producer import ProducerPlugin
from ddtrace._trace.tracing_plugins.base.consumer import ConsumerPlugin


class KafkaProducePlugin(ProducerPlugin):
    """Handles kafka.produce events."""

    package = "kafka"
    operation = "produce"


class KafkaConsumePlugin(ConsumerPlugin):
    """Handles kafka.consume events."""

    package = "kafka"
    operation = "consume"
```

---

## Part 5: Plugin Registration

### Location: `ddtrace/_trace/tracing_plugins/__init__.py`

```python
"""
Integration tracing plugins.

This module provides the plugin system for tracing integrations.
"""
from typing import List, Type

# Base classes
from ddtrace._trace.tracing_plugins.base.tracing import TracingPlugin


# All registered plugins
_plugins: List[TracingPlugin] = []


def register(plugin_cls: Type[TracingPlugin]) -> None:
    """Register a plugin class."""
    plugin = plugin_cls()
    plugin.register()
    _plugins.append(plugin)


def initialize() -> None:
    """Initialize all v2 integration plugins."""
    # Import contrib plugins
    from ddtrace._trace.tracing_plugins.contrib import asyncpg
    from ddtrace._trace.tracing_plugins.contrib import psycopg
    from ddtrace._trace.tracing_plugins.contrib import httpx
    from ddtrace._trace.tracing_plugins.contrib import flask
    from ddtrace._trace.tracing_plugins.contrib import kafka

    # Database plugins
    register(asyncpg.AsyncpgExecutePlugin)
    register(asyncpg.AsyncpgConnectPlugin)
    register(psycopg.PsycopgPlugin)

    # HTTP client plugins
    register(httpx.HttpxPlugin)

    # Web framework plugins
    register(flask.FlaskPlugin)

    # Messaging plugins
    register(kafka.KafkaProducePlugin)
    register(kafka.KafkaConsumePlugin)
```

---

## Summary

### Plugin Hierarchy (Final)

```
TracingPlugin
│
├── OutboundPlugin (TO external services)
│   │
│   ├── ClientPlugin (kind: CLIENT, type: web)
│   │   └── StoragePlugin (type: storage)
│   │       └── DatabasePlugin (type: sql)
│   │
│   └── ProducerPlugin (kind: PRODUCER, type: worker)
│
└── InboundPlugin (FROM external sources)
    │
    ├── ServerPlugin (kind: SERVER, type: web)
    │   └── RouterPlugin
    │
    └── ConsumerPlugin (kind: CONSUMER, type: worker)
```

### Adding a New Integration

1. **Patch side** (`contrib/auto/{pkg}/patch.py`):
   - Emit `{pkg}.{operation}` event with appropriate context
   - ~30 lines of code

2. **Plugin side** (`_trace/tracing_plugins/contrib/{pkg}.py`):
   - Extend appropriate base class
   - Define `package`, `operation`
   - Override only what's different
   - ~10-20 lines of code

### Benefits

| Aspect | Impact |
|--------|--------|
| Consistency | All DBs get same tags, all HTTP clients behave same |
| Clear hierarchy | Outbound vs Inbound split mirrors data flow |
| Code reduction | New integration: ~50 lines vs ~200+ lines |
| Maintainability | Fix DBM once, all DBs get it |
| Testability | Test base classes once, test overrides in isolation |
| AI-friendly | Simple patterns for agentic code generation |
