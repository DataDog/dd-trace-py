# Abstraction of dd-trace-py integrations

**Authors:** William Conti, Munir Abdinur
**Date:** Aug 19, 2025

---

## Overview - The Problem

Currently, the dd-trace-py integrations lack consistency. Many were implemented as one-off projects and can drastically differ in traced operations and data semantics such as span tags. When developing across a class of integrations, such as adding a new tag or feature in all HTTP clients, engineers are required to touch each individual HTTP client integration, drastically slowing development times compared to other libraries. We also lack a common API used for integrations, which further hurts consistency across integration implementations.

## Why?

By implementing a common abstraction pattern for integrations, similar to that used by dd-trace-js, we will be able to drastically reduce development time in the libraries for shared features of common integration types. The abstraction pattern will allow for common code paths between similar integrations. Additionally, a common instrumentation API will allow for more consistency in semantics such as operation / service naming and tag usage.

An example of the current-day difference in development times can be seen by the implementation of inferred proxy spans (to support API Gateway tracing) for all HTTP server integrations between the dd-trace-js PR and dd-trace-py PR.

**dd-trace-js: Supporting API Gateway**
- 14 days from PR creation to feature completion and merge
- 4 files touched
- 1 engineer working on feature

**dd-trace-py: Supporting API Gateway**
- 28 days from PR creation to feature completion and merge
- 30 files touched
- 2 engineers working on feature

Additionally, when implementing new integrations, it is easy to forget to implement certain features that should be supported by that type of integration. IE: a new database integration and an engineer remembering to implement DBM comment propagation.

## Why not?

Abstraction of the integrations does not provide immediate value. The value produced by this change will be a reduction in future development time, as well as improved data semantics.

---

## TL;DR - Quick Summary

**The Core Idea:** Split integration code into two parts with a data struct as the contract between them.

```
┌────────────────────────┐     ┌────────────────────────┐     ┌────────────────────────┐
│     INSTRUMENTATION    │     │       DATA STRUCT      │     │        HANDLER         │
│     (Producer Side)    │     │        (Event)         │     │    (Consumer Side)     │
├────────────────────────┤     ├────────────────────────┤     ├────────────────────────┤
│                        │     │                        │     │                        │
│  • Patches library     │     │  • DatabaseEvent       │     │  • Creates span        │
│  • Extracts data       │ ──▶ │  • HTTPClientEvent     │ ──▶ │  • Applies tags        │
│  • Fills struct        │     │  • KafkaEvent          │     │  • Handles DBM/DSM     │
│  • Calls trace_event() │     │  • MessagingEvent      │     │  • Finishes span       │
│                        │     │  • etc.                │     │                        │
└────────────────────────┘     └────────────────────────┘     └────────────────────────┘
```

**Key Design Decisions:**

1. **Event structs as data contracts** - Typed dataclasses (e.g., `DatabaseEvent`, `KafkaEvent`) where field names directly become span tags. No mapping layer needed.

2. **Handler class hierarchy** - Mirrors event hierarchy. `KafkaEventHandler` extends `MessagingEventHandler`, inheriting distributed tracing logic and adding DSM checkpoints.

3. **Context manager API** - Clean span lifecycle via `with core.trace_event(event) as span:`. Span auto-finishes on exit, errors handled automatically.

4. **Instrumentation just extracts data** - No span creation logic in patches. Just fill the struct and call `trace_event()`.

**Example - What Integration Code Looks Like:**

```python
# Instrumentation side (producer)
def _wrap_execute(self, wrapped, instance, args, kwargs):
    event = DatabaseEvent(
        _span_name="postgres.query",
        _resource=query,
        db_system="postgresql",
        db_statement=query,
        out_host=host,
        out_port=port,
    )

    with core.trace_event(event) as span:
        result = wrapped(*args, **kwargs)
        event.db_row_count = len(result)  # Update before exit
        return result
    # Span finished, tags applied, errors handled
```

**Benefits:**
- Add a new tag to all databases? Add field to `DatabaseEvent`, done.
- Add DBM to a new database? It inherits from `DatabaseEventHandler`, done.
- Consistent semantics across all integrations of the same type.
- Clear separation: patches extract data, handlers create spans.

---

## Solution

We implement a common instrumentation and plugin interface utilizing an event-driven architecture. Two separate interfaces are created:

1. **Instrumentation Interface (Producer)**: Responsible for patching libraries, extracting data, and producing typed event structs
2. **Trace Handler (Consumer)**: Receives event structs and translates them to spans with consistent tagging

The key insight is **separation of concerns** - the instrumentation code only extracts data into structs, while a single handler translates those structs to properly tagged spans.

### Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          PRODUCER SIDE                                      │
│                    (Instrumentation Interface)                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  AsyncpgInstrumentation   RequestsInstrumentation   KafkaInstrumentation    │
│          │                        │                        │                │
│          │ patch()                │ patch()                │ patch()        │
│          ▼                        ▼                        ▼                │
│    Wraps execute()          Wraps send()           Wraps produce()          │
│          │                        │                        │                │
│          ▼                        ▼                        ▼                │
│    DatabaseEvent           HTTPClientEvent            KafkaEvent            │
│    (filled struct)         (filled struct)         (filled struct)          │
│          │                        │                        │                │
│          └────────────────────────┼────────────────────────┘                │
│                                   ▼                                         │
│                   with core.trace_event(event) as span:                     │
│                       result = wrapped(*args, **kwargs)                     │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                          CONSUMER SIDE                                      │
│                   (Handler Class Hierarchy)                                 │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│                        get_handler(event_type)                              │
│                                   │                                         │
│         ┌─────────────────────────┼─────────────────────────┐               │
│         │                         │                         │               │
│         ▼                         ▼                         ▼               │
│  ┌─────────────────┐    ┌─────────────────┐    ┌───────────────────────┐    │
│  │ DatabaseEvent   │    │ HTTPClientEvent │    │ KafkaEvent            │    │
│  │       │         │    │       │         │    │       │               │    │
│  │       ▼         │    │       ▼         │    │       ▼               │    │
│  │ DatabaseEvent   │    │ HTTPClientEvent │    │ KafkaEventHandler     │    │
│  │ Handler         │    │ Handler         │    │       │               │    │
│  │                 │    │                 │    │  calls super() ───────┼─┐  │
│  │ • DBM inject    │    │ • Header inject │    │       │               │ │  │
│  │ • Peer service  │    │ • Peer service  │    │       ▼               │ │  │
│  └─────────────────┘    └─────────────────┘    │ MessagingEventHandler │ │  │
│                                                │                       │ │  │
│                                                │ • Inject headers      │ │  │
│                                                │ • Extract & link      │ │  │
│                                                │ • Peer service        │ │  │
│                                                │       │               │ │  │
│                                                │       ▼               │ │  │
│                                                │ + Kafka-specific:     │ │  │
│                                                │ • DSM checkpoints     │ │  │
│                                                │ • Partition/offset    │ │  │
│                                                └───────────────────────┘ │  │
│                                                          ▲               │  │
│                                                          └───────────────┘  │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                    SpanEventHandler (base)                          │    │
│  │                                                                     │    │
│  │  • create_span(event) - Create span with name/resource/type         │    │
│  │  • apply_tags(span, event) - Map event fields to span tags          │    │
│  │  • on_finish(span, event) - Re-apply tags, type-specific cleanup    │    │
│  │  • trace(event) - Context manager for full lifecycle                │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                             │
│  Handler Inheritance:                                                       │
│                                                                             │
│  SpanEventHandler (base)                                                    │
│  ├── DatabaseEventHandler      (+ DBM, peer service)                        │
│  ├── HTTPClientEventHandler    (+ header injection, peer service)           │
│  ├── HTTPServerEventHandler    (+ context extraction)                       │
│  └── MessagingEventHandler     (+ inject/extract, span linking)             │
│      ├── KafkaEventHandler     (+ DSM checkpoints)                          │
│      ├── RabbitMQEventHandler  (+ AMQP-specific logic)                      │
│      └── SQSEventHandler       (+ SQS-specific logic)                       │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Flow Summary:**
1. **Instrumentation** patches library methods and creates typed event structs
2. **`trace_event()`** dispatches to the appropriate handler via `get_handler(event_type)`
3. **Handler** creates span, applies type-specific logic (DBM, DSM, etc.), manages lifecycle
4. **Span** auto-finishes when context manager exits

---

## Instrumentation Interface (Producer Side)

The instrumentation interface provides a common API for all integration patches. It handles configuration, version checking, and applying/removing patches from target libraries.

### Base Instrumentation Class

```python
from typing import List, Optional, Tuple
from abc import ABC, abstractmethod


class InstrumentationPlugin(ABC):
    """
    Base class for instrumentation plugins (publishers).

    This class is responsible for:
    - Configuration management
    - Version checking and compatibility
    - Applying/removing patches from target libraries
    - Providing trace helpers to submit data via event structs

    Subclasses implement patch() and unpatch() with library-specific logic.
    """

    name: str = None                        # Integration name (e.g., "asyncpg", "requests")
    supported_versions: str = None          # Version constraint (e.g., ">=0.22.0")
    operations: List[str] = []              # Names of operations traced (e.g., ["execute", "connect"])

    def __init__(self):
        self._patched = False
        self._config = {}                   # Per-integration configuration

    def configure(self, config: dict) -> None:
        """
        Configure the plugin with integration-specific settings.

        Called by the plugin loader during initialization.
        """
        self._config = config

    @property
    def integration_config(self):
        """Get config from ddtrace.config for this integration."""
        from ddtrace import config
        return getattr(config, self.name, {})

    @abstractmethod
    def patch(self) -> None:
        """
        Apply patches to the target library.

        Implementation should:
        1. Check if library is installed
        2. Verify version compatibility
        3. Apply wrapt wrappers to target methods
        4. Set self._patched = True
        """
        pass

    @abstractmethod
    def unpatch(self) -> None:
        """
        Remove patches from the target library.

        Implementation should:
        1. Restore original methods
        2. Set self._patched = False
        """
        pass

    def get_version(self) -> Optional[str]:
        """Get the installed version of the target library."""
        try:
            from importlib.metadata import version
            return version(self.name)
        except Exception:
            return None

    def get_versions(self) -> dict:
        """
        Return versions for the instrumented library and dependencies.

        Override to include additional dependency versions.
        """
        return {self.name: self.get_version()}

    def is_supported(self) -> bool:
        """Check if the installed library version is supported."""
        if not self.supported_versions:
            return True

        version = self.get_version()
        if not version:
            return False

        try:
            from packaging.specifiers import SpecifierSet
            from packaging.version import Version
            return Version(version) in SpecifierSet(self.supported_versions)
        except Exception:
            return True

    @property
    def is_patched(self) -> bool:
        """Check if patches are currently applied."""
        return self._patched
```

### Specialized Instrumentation Classes

Similar sets of integrations share common functionality through specialized base classes:

```python
class DatabaseInstrumentation(InstrumentationPlugin):
    """
    Base instrumentation for database clients.

    Provides:
    - Common connection info extraction patterns
    - DBM (Database Monitoring) propagation helpers
    - Query sanitization utilities
    """

    db_system: str = None  # "postgresql", "mysql", "mongodb", etc.

    def create_database_event(
        self,
        span_name: str,
        query: Optional[str] = None,
        host: Optional[str] = None,
        port: Optional[int] = None,
        database: Optional[str] = None,
        user: Optional[str] = None,
        **extra_tags
    ) -> "DatabaseEvent":
        """Helper to create a DatabaseEvent with common fields."""
        from ddtrace._trace.events import DatabaseEvent

        return DatabaseEvent(
            _span_name=span_name,
            _resource=query,
            _integration_name=self.name,
            component=self.name,
            db_system=self.db_system,
            db_statement=query,
            db_name=database,
            db_user=user,
            out_host=host,
            out_port=port,
            **extra_tags
        )


class HTTPClientInstrumentation(InstrumentationPlugin):
    """
    Base instrumentation for HTTP clients.

    Provides:
    - URL parsing utilities
    - Header injection for distributed tracing
    - Response status handling
    """

    def create_http_client_event(
        self,
        method: str,
        url: str,
        headers: Optional[dict] = None,
        inject_callback: Optional[callable] = None,
        **extra_tags
    ) -> "HTTPClientEvent":
        """Helper to create an HTTPClientEvent with common fields."""
        from ddtrace._trace.events import HTTPClientEvent
        from urllib.parse import urlparse

        parsed = urlparse(url)

        return HTTPClientEvent(
            _span_name=f"{self.name}.request",
            _resource=f"{method} {parsed.path or '/'}",
            _integration_name=self.name,
            component=self.name,
            http_method=method,
            http_url=url,
            http_target=parsed.path,
            net_peer_name=parsed.hostname,
            net_peer_port=parsed.port,
            _request_headers=headers or {},
            _inject_headers_callback=inject_callback,
            **extra_tags
        )


class MessagingInstrumentation(InstrumentationPlugin):
    """
    Base instrumentation for message brokers.

    Provides:
    - Producer/consumer span creation
    - Message header injection/extraction
    - Batch message handling
    """

    messaging_system: str = None  # "kafka", "rabbitmq", "sqs", etc.

    def create_producer_event(
        self,
        destination: str,
        key: Optional[str] = None,
        inject_callback: Optional[callable] = None,
        **extra_tags
    ) -> "MessagingEvent":
        """Helper to create a produce event."""
        from ddtrace._trace.events import MessagingEvent

        return MessagingEvent(
            _span_name=f"{self.messaging_system}.produce",
            _resource=destination,
            _integration_name=self.name,
            component=self.name,
            span_kind="producer",
            messaging_system=self.messaging_system,
            messaging_destination_name=destination,
            messaging_operation="publish",
            _inject_headers_callback=inject_callback,
            **extra_tags
        )

    def create_consumer_event(
        self,
        destination: str,
        headers: Optional[dict] = None,
        **extra_tags
    ) -> "MessagingEvent":
        """Helper to create a consume event."""
        from ddtrace._trace.events import MessagingEvent

        return MessagingEvent(
            _span_name=f"{self.messaging_system}.consume",
            _resource=destination,
            _integration_name=self.name,
            component=self.name,
            span_kind="consumer",
            messaging_system=self.messaging_system,
            messaging_destination_name=destination,
            messaging_operation="receive",
            _propagation_headers=headers or {},
            **extra_tags
        )


class WebFrameworkInstrumentation(InstrumentationPlugin):
    """
    Base instrumentation for web frameworks.

    Provides:
    - Request span creation
    - Distributed tracing context extraction
    - Response status handling
    - Route/endpoint extraction patterns
    """

    def create_request_event(
        self,
        method: str,
        url: str,
        path: str,
        headers: Optional[dict] = None,
        client_ip: Optional[str] = None,
        **extra_tags
    ) -> "HTTPServerEvent":
        """Helper to create a request event."""
        from ddtrace._trace.events import HTTPServerEvent

        return HTTPServerEvent(
            _span_name=f"{self.name}.request",
            _resource=f"{method} {path}",
            _integration_name=self.name,
            component=self.name,
            http_method=method,
            http_url=url,
            http_target=path,
            http_client_ip=client_ip,
            _request_headers=headers or {},
            **extra_tags
        )
```

---

## Data Structs (Event Types)

Instead of multiple plugin classes with `on_start`/`on_finish` methods, we define **data structs** for each class of integration. Each struct has fields that directly map to span attributes and tags.

**Key principle**: Field names ARE the tag names. No mapping needed.
- Field `db_system` → tag `db_system`
- Field `messaging_destination_name` → tag `messaging_destination_name`

### Base Event Class

```python
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class SpanEvent:
    """
    Base class for all span events.

    Contains core span attributes that all integrations share.
    Fields starting with '_' are span attributes (not tags).
    All other fields become span tags directly.
    """

    # --- Core span attributes (not tags, prefixed with _) ---
    _span_name: str                         # Operation name (e.g., "postgres.query")
    _resource: Optional[str] = None         # Resource name (e.g., "SELECT * FROM users")
    _service: Optional[str] = None          # Service name override
    _span_type: Optional[str] = None        # Span type (sql, web, http, worker, etc.)
    _integration_name: str = ""             # Integration name for config lookup

    # --- Standard tags (field name = tag name) ---
    component: Optional[str] = None         # component tag
    span_kind: Optional[str] = None         # span.kind tag

    def get_tags(self) -> Dict[str, Any]:
        """
        Return all public fields as tags.

        Fields starting with '_' are span attributes, not tags.
        """
        return {
            k: v for k, v in self.__dict__.items()
            if not k.startswith('_') and v is not None
        }
```

### Database Event

```python
@dataclass
class DatabaseEvent(SpanEvent):
    """
    Event for database operations.

    Field names follow OTel semantic conventions.
    """

    _span_type: str = "sql"
    span_kind: str = "client"

    # Database identification
    db_system: Optional[str] = None         # postgresql, mysql, mongodb, etc.
    db_name: Optional[str] = None           # database name
    db_user: Optional[str] = None
    db_statement: Optional[str] = None      # the query
    db_operation: Optional[str] = None      # SELECT, INSERT, etc.
    db_row_count: Optional[int] = None      # set on finish

    # Connection info
    out_host: Optional[str] = None
    out_port: Optional[int] = None
    server_address: Optional[str] = None

    # DBM (Database Monitoring) - internal use, not tags
    _dbm_propagator: Optional[Any] = None
```

### Messaging Event

```python
@dataclass
class MessagingEvent(SpanEvent):
    """
    Event for messaging operations (Kafka, RabbitMQ, SQS, etc.).
    """

    _span_type: str = "worker"

    # Messaging identification
    messaging_system: Optional[str] = None
    messaging_destination_name: Optional[str] = None
    messaging_operation: Optional[str] = None        # publish, receive, process
    messaging_message_id: Optional[str] = None

    # Connection info
    net_peer_name: Optional[str] = None
    net_peer_port: Optional[int] = None

    # Batch info
    messaging_batch_message_count: Optional[int] = None

    # Internal use (not tags)
    _propagation_headers: Dict[str, str] = field(default_factory=dict)
    _inject_headers_callback: Optional[Any] = None
```

### Kafka Event (Extension Example)

When a specific integration needs additional tags beyond the base struct:

```python
@dataclass
class KafkaEvent(MessagingEvent):
    """
    Kafka-specific event extending MessagingEvent.
    """

    messaging_system: str = "kafka"

    # Kafka-specific tags (field name = tag name)
    messaging_kafka_partition: Optional[int] = None
    messaging_kafka_offset: Optional[int] = None
    messaging_kafka_message_key: Optional[str] = None
    messaging_kafka_consumer_group: Optional[str] = None
    messaging_kafka_tombstone: Optional[bool] = None
```

### HTTP Client Event

```python
@dataclass
class HTTPClientEvent(SpanEvent):
    """
    Event for outbound HTTP requests.
    """

    _span_type: str = "http"
    span_kind: str = "client"

    # HTTP request
    http_method: Optional[str] = None
    http_url: Optional[str] = None
    http_target: Optional[str] = None
    http_status_code: Optional[int] = None  # set on finish

    # Connection info
    net_peer_name: Optional[str] = None
    net_peer_port: Optional[int] = None

    # Internal use
    _request_headers: Dict[str, str] = field(default_factory=dict)
    _inject_headers_callback: Optional[Any] = None
```

### HTTP Server Event

```python
@dataclass
class HTTPServerEvent(SpanEvent):
    """
    Event for inbound HTTP requests (web frameworks).
    """

    _span_type: str = "web"
    span_kind: str = "server"

    # HTTP request
    http_method: Optional[str] = None
    http_url: Optional[str] = None
    http_route: Optional[str] = None
    http_target: Optional[str] = None
    http_user_agent: Optional[str] = None
    http_status_code: Optional[int] = None  # set on finish

    # Client info
    http_client_ip: Optional[str] = None
    net_peer_ip: Optional[str] = None

    # Internal use
    _request_headers: Dict[str, str] = field(default_factory=dict)
```

---

## Handler Class Hierarchy (Consumer Side)

The consumer side uses a **handler class hierarchy** that mirrors the event class hierarchy. Each handler knows how to process its specific event type, keeping type-specific logic isolated and maintainable.

### Why a Handler Hierarchy?

With 15+ event types, a single function handling all types would become unwieldy. Instead:
- Each handler class owns its event type's span creation logic
- Handler hierarchy mirrors event hierarchy (e.g., `KafkaEventHandler` extends `MessagingEventHandler`)
- Type-specific features (DBM, DSM, peer service) are isolated in the appropriate handler
- Easy to extend - just subclass the handler

### Base Handler Class

```python
from abc import ABC
from contextlib import contextmanager, asynccontextmanager
from typing import Type, Dict, Generator
import sys


class SpanEventHandler(ABC):
    """
    Base handler for all span events.

    Provides the core span lifecycle management via context managers.
    Subclasses override hooks for type-specific behavior.
    """

    event_type: Type[SpanEvent] = SpanEvent

    @contextmanager
    def trace(self, event: SpanEvent) -> Generator[Span, None, None]:
        """
        Context manager that handles full span lifecycle.

        Usage:
            handler = get_handler(event)
            with handler.trace(event) as span:
                result = do_work()
                event.db_row_count = len(result)
            # Span automatically finished
        """
        span = self.create_span(event)
        try:
            yield span
        except Exception:
            span.set_exc_info(*sys.exc_info())
            raise
        finally:
            self.on_finish(span, event)
            span.finish()

    @asynccontextmanager
    async def trace_async(self, event: SpanEvent):
        """Async version of trace() for async integrations."""
        span = self.create_span(event)
        try:
            yield span
        except Exception:
            span.set_exc_info(*sys.exc_info())
            raise
        finally:
            self.on_finish(span, event)
            span.finish()

    def create_span(self, event: SpanEvent) -> Span:
        """
        Create span with base attributes and tags.

        Override in subclasses to add type-specific setup.
        """
        from ddtrace import tracer

        span = tracer.trace(
            event._span_name,
            service=event._service,
            resource=event._resource,
            span_type=event._span_type,
        )
        self.apply_tags(span, event)
        return span

    def apply_tags(self, span: Span, event: SpanEvent) -> None:
        """Apply all public fields from event as span tags."""
        for field_name, value in event.get_tags().items():
            if value is not None:
                if isinstance(value, (int, float)):
                    span.set_metric(field_name, value)
                else:
                    span._set_tag_str(field_name, str(value))

    def on_finish(self, span: Span, event: SpanEvent) -> None:
        """
        Hook called before span.finish().

        Override in subclasses for type-specific cleanup.
        Default implementation re-applies tags to capture updates.
        """
        self.apply_tags(span, event)
```

### Database Event Handler

```python
class DatabaseEventHandler(SpanEventHandler):
    """
    Handler for database events.

    Adds:
    - DBM (Database Monitoring) comment propagation
    - Peer service resolution for database connections
    """

    event_type = DatabaseEvent

    # Priority order for peer service resolution
    peer_service_tags = ["db_name", "out_host", "server_address"]

    def create_span(self, event: DatabaseEvent) -> Span:
        """Create span and handle DBM injection."""
        span = super().create_span(event)
        self._handle_dbm(span, event)
        return span

    def on_finish(self, span: Span, event: DatabaseEvent) -> None:
        """Finish with peer service tagging."""
        super().on_finish(span, event)
        self._set_peer_service(span, event)

    def _handle_dbm(self, span: Span, event: DatabaseEvent) -> None:
        """Inject DBM trace context into query comments."""
        if event._dbm_propagator and event.db_statement:
            event._dbm_propagator.inject(span, event)

    def _set_peer_service(self, span: Span, event: DatabaseEvent) -> None:
        """Set peer.service from database connection info."""
        from ddtrace import config

        if not getattr(config, "_peer_service_computation_enabled", True):
            return
        if span.get_tag("peer.service"):
            return

        for tag in self.peer_service_tags:
            if value := span.get_tag(tag):
                span._set_tag_str("peer.service", str(value))
                break
```

### HTTP Client Event Handler

```python
class HTTPClientEventHandler(SpanEventHandler):
    """
    Handler for HTTP client events.

    Adds:
    - Distributed tracing header injection
    - Peer service resolution
    """

    event_type = HTTPClientEvent

    peer_service_tags = ["net_peer_name", "out_host", "http_host"]

    def create_span(self, event: HTTPClientEvent) -> Span:
        """Create span and inject distributed tracing headers."""
        span = super().create_span(event)
        self._inject_distributed_tracing(span, event)
        return span

    def on_finish(self, span: Span, event: HTTPClientEvent) -> None:,
        """Finish with peer service tagging."""
        super().on_finish(span, event)
        self._set_peer_service(span, event)

    def _inject_distributed_tracing(self, span: Span, event: HTTPClientEvent) -> None:
        """Inject trace context into outbound request headers."""
        if event._inject_headers_callback:
            from ddtrace.propagation.http import HTTPPropagator
            headers = {}
            HTTPPropagator.inject(span.context, headers)
            event._inject_headers_callback(headers)

    def _set_peer_service(self, span: Span, event: HTTPClientEvent) -> None:
        """Set peer.service from HTTP target info."""
        from ddtrace import config

        if not getattr(config, "_peer_service_computation_enabled", True):
            return
        if span.get_tag("peer.service"):
            return

        for tag in self.peer_service_tags:
            if value := span.get_tag(tag):
                span._set_tag_str("peer.service", str(value))
                break
```

### HTTP Server Event Handler

```python
class HTTPServerEventHandler(SpanEventHandler):
    """
    Handler for HTTP server (web framework) events.

    Adds:
    - Distributed tracing context extraction
    - Request span activation
    """

    event_type = HTTPServerEvent

    def create_span(self, event: HTTPServerEvent) -> Span:
        """Create span and extract distributed tracing context."""
        # Extract parent context before creating span
        parent_ctx = self._extract_distributed_tracing(event)

        span = super().create_span(event)

        # Activate extracted context as parent
        if parent_ctx:
            span.context._parent = parent_ctx

        return span

    def _extract_distributed_tracing(self, event: HTTPServerEvent):
        """Extract trace context from inbound request headers."""
        if event._request_headers:
            from ddtrace.propagation.http import HTTPPropagator
            return HTTPPropagator.extract(event._request_headers)
        return None
```

### Messaging Event Handler

```python
class MessagingEventHandler(SpanEventHandler):
    """
    Handler for messaging events (Kafka, RabbitMQ, SQS, etc.).

    Adds:
    - Distributed tracing injection (producers)
    - Distributed tracing extraction and span linking (consumers)
    - Peer service resolution
    """

    event_type = MessagingEvent

    peer_service_tags = ["messaging_destination_name", "net_peer_name"]

    def create_span(self, event: MessagingEvent) -> Span:
        """Create span with distributed tracing handling."""
        span = super().create_span(event)

        if event.span_kind == "producer":
            self._inject_distributed_tracing(span, event)
        elif event.span_kind == "consumer":
            self._extract_and_link(span, event)

        return span

    def on_finish(self, span: Span, event: MessagingEvent) -> None:
        """Finish with peer service tagging for producers."""
        super().on_finish(span, event)
        if event.span_kind == "producer":
            self._set_peer_service(span, event)

    def _inject_distributed_tracing(self, span: Span, event: MessagingEvent) -> None:
        """Inject trace context into message headers."""
        if event._inject_headers_callback:
            from ddtrace.propagation.http import HTTPPropagator
            headers = {}
            HTTPPropagator.inject(span.context, headers)
            event._inject_headers_callback(headers)

    def _extract_and_link(self, span: Span, event: MessagingEvent) -> None:
        """Extract trace context and link to producer span."""
        if event._propagation_headers:
            from ddtrace.propagation.http import HTTPPropagator
            ctx = HTTPPropagator.extract(event._propagation_headers)
            if ctx:
                span.link_span(ctx)

    def _set_peer_service(self, span: Span, event: MessagingEvent) -> None:
        """Set peer.service from messaging destination."""
        from ddtrace import config

        if not getattr(config, "_peer_service_computation_enabled", True):
            return
        if span.get_tag("peer.service"):
            return

        for tag in self.peer_service_tags:
            if value := span.get_tag(tag):
                span._set_tag_str("peer.service", str(value))
                break
```

### Kafka Event Handler (Extension Example)

```python
class KafkaEventHandler(MessagingEventHandler):
    """
    Handler for Kafka events.

    Extends MessagingEventHandler with:
    - Data Streams Monitoring (DSM) checkpoint handling
    - Kafka-specific finish logic
    """

    event_type = KafkaEvent

    def create_span(self, event: KafkaEvent) -> Span:
        """Create span with DSM produce checkpoint."""
        span = super().create_span(event)

        if event.span_kind == "producer":
            self._handle_dsm_produce(span, event)

        return span

    def on_finish(self, span: Span, event: KafkaEvent) -> None:
        """Finish with DSM consume checkpoint."""
        super().on_finish(span, event)

        if event.span_kind == "consumer":
            self._handle_dsm_consume(span, event)

    def _handle_dsm_produce(self, span: Span, event: KafkaEvent) -> None:
        """Set DSM checkpoint for produced messages."""
        from ddtrace.data_streams import set_produce_checkpoint
        # DSM produce checkpoint logic
        pass

    def _handle_dsm_consume(self, span: Span, event: KafkaEvent) -> None:
        """Set DSM checkpoint for consumed messages."""
        from ddtrace.data_streams import set_consume_checkpoint
        # DSM consume checkpoint logic
        pass
```

### Handler Registry and Dispatch

```python
from typing import Dict, Type


# Registry maps event types to handler instances
HANDLER_REGISTRY: Dict[Type[SpanEvent], SpanEventHandler] = {
    SpanEvent: SpanEventHandler(),
    DatabaseEvent: DatabaseEventHandler(),
    HTTPClientEvent: HTTPClientEventHandler(),
    HTTPServerEvent: HTTPServerEventHandler(),
    MessagingEvent: MessagingEventHandler(),
    KafkaEvent: KafkaEventHandler(),
    # Add more handlers as needed...
}


def get_handler(event_type: Type[SpanEvent]) -> SpanEventHandler:
    """
    Get the appropriate handler for an event type.

    Walks up the MRO to find the most specific handler.
    """
    for cls in event_type.__mro__:
        if cls in HANDLER_REGISTRY:
            return HANDLER_REGISTRY[cls]
    return HANDLER_REGISTRY[SpanEvent]


def register_handler(event_type: Type[SpanEvent], handler: SpanEventHandler) -> None:
    """Register a custom handler for an event type."""
    HANDLER_REGISTRY[event_type] = handler
```

### Core API - trace_event()

```python
from contextlib import contextmanager, asynccontextmanager


@contextmanager
def trace_event(event: SpanEvent):
    """
    Context manager for tracing an event.

    Dispatches to the appropriate handler based on event type.

    Usage:
        event = DatabaseEvent(
            _span_name="postgres.query",
            _resource=query,
            db_system="postgresql",
            db_statement=query,
        )
        with core.trace_event(event) as span:
            result = execute_query(query)
            event.db_row_count = len(result)  # Can update event during execution
        # Span is automatically finished here
    """
    handler = get_handler(type(event))
    with handler.trace(event) as span:
        yield span


@asynccontextmanager
async def trace_event_async(event: SpanEvent):
    """
    Async context manager for tracing events.

    Usage:
        async with core.trace_event_async(event) as span:
            result = await execute_query(query)
            event.db_row_count = len(result)
    """
    handler = get_handler(type(event))
    async with handler.trace_async(event) as span:
        yield span
```

### Handler Hierarchy Diagram

```
SpanEventHandler (base)
├── DatabaseEventHandler
│   └── [PostgresEventHandler, MySQLEventHandler, etc. if needed]
├── HTTPClientEventHandler
│   └── [RequestsEventHandler, AIOHTTPEventHandler, etc. if needed]
├── HTTPServerEventHandler
│   └── [FlaskEventHandler, DjangoEventHandler, etc. if needed]
└── MessagingEventHandler
    ├── KafkaEventHandler
    ├── RabbitMQEventHandler
    └── SQSEventHandler
```

---

## Complete Example: asyncpg Integration

### Instrumentation Plugin

```python
# ddtrace/contrib/asyncpg/instrumentation.py

from ddtrace._trace.instrumentation import DatabaseInstrumentation
from ddtrace._trace.events import DatabaseEvent


class AsyncpgInstrumentation(DatabaseInstrumentation):
    """
    asyncpg instrumentation plugin.

    Traces:
    - Protocol.execute (queries)
    - connect (connections)
    """

    name = "asyncpg"
    supported_versions = ">=0.22.0"
    operations = ["execute", "connect"]
    db_system = "postgresql"

    def patch(self) -> None:
        """Apply patches to asyncpg."""
        if not self.is_supported():
            return

        from ddtrace.vendor.wrapt import wrap_function_wrapper

        wrap_function_wrapper(
            "asyncpg.protocol",
            "Protocol.execute",
            self._wrap_execute
        )

        wrap_function_wrapper(
            "asyncpg",
            "connect",
            self._wrap_connect
        )

        self._patched = True

    def unpatch(self) -> None:
        """Remove patches from asyncpg."""
        import asyncpg
        from ddtrace.vendor.wrapt import unwrap

        unwrap(asyncpg.protocol.Protocol, "execute")
        unwrap(asyncpg, "connect")

        self._patched = False

    async def _wrap_execute(self, wrapped, instance, args, kwargs):
        """Wrap Protocol.execute to trace queries."""
        from ddtrace.internal import core

        event = self._create_execute_event(instance, args, kwargs)

        async with core.trace_event_async(event) as span:
            result = await wrapped(*args, **kwargs)

            # Update row count on finish
            if hasattr(result, '__len__'):
                event.db_row_count = len(result)

            return result

    async def _wrap_connect(self, wrapped, instance, args, kwargs):
        """Wrap connect to trace connection creation."""
        from ddtrace.internal import core

        event = self._create_connect_event(args, kwargs)

        async with core.trace_event_async(event) as span:
            return await wrapped(*args, **kwargs)

    def _create_execute_event(self, instance, args, kwargs) -> DatabaseEvent:
        """Extract context from Protocol.execute()."""

        # Extract query
        state = args[0] if args else kwargs.get("state")
        if isinstance(state, bytes):
            query = state.decode("utf-8", errors="replace")
        elif isinstance(state, str):
            query = state
        else:
            query = getattr(state, "query", None)

        # Extract connection info
        conn = getattr(instance, "_connection", None)
        addr = getattr(conn, "_addr", None) if conn else None
        params = getattr(conn, "_params", None) if conn else None

        host = addr[0] if addr and len(addr) >= 2 else None
        port = addr[1] if addr and len(addr) >= 2 else None

        return self.create_database_event(
            span_name="postgres.query",
            query=query,
            host=host,
            port=port,
            database=getattr(params, "database", None) if params else None,
            user=getattr(params, "user", None) if params else None,
        )

    def _create_connect_event(self, args, kwargs) -> DatabaseEvent:
        """Extract context from connect()."""

        host = kwargs.get("host") or (args[1] if len(args) > 1 else None)
        port = kwargs.get("port") or (args[2] if len(args) > 2 else None)
        user = kwargs.get("user") or (args[3] if len(args) > 3 else None)
        database = kwargs.get("database") or (args[4] if len(args) > 4 else None)

        return self.create_database_event(
            span_name="postgres.connect",
            host=host,
            port=port,
            database=database,
            user=user,
        )
```

### No Subscriber Plugin Needed!

With this architecture, **there's no subscriber plugin class needed**. The `trace_event_async()` context manager automatically:
- Creates the span with correct name/resource/type
- Maps all fields to tags
- Handles peer service
- Handles errors
- Finishes the span on exit

---

## Complete Example: Kafka Integration

```python
# ddtrace/contrib/kafka/instrumentation.py

from ddtrace._trace.instrumentation import MessagingInstrumentation
from ddtrace._trace.events import KafkaEvent


class KafkaInstrumentation(MessagingInstrumentation):
    """
    Kafka instrumentation plugin.

    Traces:
    - KafkaProducer.send (produce)
    - KafkaConsumer.poll (consume)
    """

    name = "kafka"
    supported_versions = ">=1.4.0"
    operations = ["produce", "consume"]
    messaging_system = "kafka"

    def patch(self) -> None:
        from ddtrace.vendor.wrapt import wrap_function_wrapper

        wrap_function_wrapper(
            "kafka",
            "KafkaProducer.send",
            self._wrap_send
        )

        wrap_function_wrapper(
            "kafka",
            "KafkaConsumer.poll",
            self._wrap_poll
        )

        self._patched = True

    def _wrap_send(self, wrapped, instance, args, kwargs):
        """Wrap KafkaProducer.send()."""
        from ddtrace.internal import core

        topic = args[0] if args else kwargs.get("topic")
        key = kwargs.get("key")

        # Create callback to inject trace headers
        def inject_headers(headers_dict):
            headers = kwargs.setdefault("headers", [])
            for k, v in headers_dict.items():
                headers.append((k, v.encode() if isinstance(v, str) else v))

        event = KafkaEvent(
            _span_name="kafka.produce",
            _resource=topic,
            _integration_name=self.name,
            component=self.name,
            span_kind="producer",
            messaging_destination_name=topic,
            messaging_operation="publish",
            messaging_kafka_message_key=key.decode() if isinstance(key, bytes) else key,
            _inject_headers_callback=inject_headers,
        )

        with core.trace_event(event) as span:
            return wrapped(*args, **kwargs)

    def _wrap_poll(self, wrapped, instance, args, kwargs):
        """Wrap KafkaConsumer.poll()."""
        from ddtrace.internal import core

        records = wrapped(*args, **kwargs)

        # Create a span for each message consumed
        for topic_partition, messages in records.items():
            for message in messages:
                event = KafkaEvent(
                    _span_name="kafka.consume",
                    _resource=message.topic,
                    _integration_name=self.name,
                    component=self.name,
                    span_kind="consumer",
                    messaging_destination_name=message.topic,
                    messaging_operation="receive",
                    messaging_kafka_partition=message.partition,
                    messaging_kafka_offset=message.offset,
                    messaging_kafka_message_key=message.key.decode() if message.key else None,
                    messaging_kafka_consumer_group=getattr(instance, "_group_id", None),
                    _propagation_headers={
                        h[0]: h[1].decode() if isinstance(h[1], bytes) else h[1]
                        for h in (message.headers or [])
                    },
                )

                with core.trace_event(event):
                    pass  # Span created and finished for each message

        return records
```

---

## Benefits of This Approach

### 1. Clean Instrumentation API

All integrations use the same patterns:
- Extend `InstrumentationPlugin` (or specialized subclass)
- Implement `patch()` and `unpatch()`
- Use helper methods like `create_database_event()`

### 2. No Explicit Tag Mapping

Field names ARE the tags. No separate mapping dictionaries.
- `event.db_system = "postgresql"` → span tag `db_system = "postgresql"`

### 3. Context Manager = Clean Lifecycle

```python
with core.trace_event(event) as span:
    result = do_work()
    event.result_count = len(result)
# Span automatically finished, tags applied, errors handled
```

### 4. Separation of Concerns

- **Instrumentation** (patches): Create event struct, fill with data
- **Handler** (trace_event): Translate struct to span
- Event struct is the contract between them

### 5. Easy Extension

```python
@dataclass
class KafkaEvent(MessagingEvent):
    messaging_kafka_partition: Optional[int] = None
    # This field automatically becomes a tag
```

### 6. No Subscriber Plugins

The `trace_event()` context manager handles everything. No need for separate plugin classes with `on_start`/`on_finish`.

---

## Migration Path

1. **Phase 1**: Implement core infrastructure
   - `InstrumentationPlugin` base class and specialized subclasses
   - Event dataclasses (SpanEvent, DatabaseEvent, etc.)
   - `trace_event()` / `trace_event_async()` context managers

2. **Phase 2**: Migrate one integration per category
   - One database (e.g., asyncpg)
   - One HTTP client (e.g., requests)
   - One messaging (e.g., kafka)
   - One web framework (e.g., flask)

3. **Phase 3**: Migrate remaining integrations

4. **Phase 4**: Deprecate old patterns

---

## Open Questions

1. **Naming convention**: Use underscores (`db_system`) or dots (`db.system`) for field/tag names?

2. **Nested spans**: How do middleware spans work? Nested `with` blocks?

3. **Long-running operations**: For streaming/polling, when does the span end?

4. **DSM/ASM hooks**: Where do product-specific hooks fit in?

5. **Pin compatibility**: How does this integrate with the existing Pin pattern?
