# Integration V2 Framework - Implementation Plan

## Overview

This document tracks the implementation status of the new event-based integration framework for dd-trace-py. The framework separates concerns between:

- **Events**: Dataclass contracts defining what data integrations collect
- **Handlers**: Classes that process events and create spans with product-specific logic
- **Instrumentation**: Classes that patch libraries and emit events

## Current Status

### Completed

#### Events (`ddtrace/_trace/integrations/events/`)

| Event Type | Location | Status | Description |
|------------|----------|--------|-------------|
| `SpanEvent` | `base/_base.py` | Done | Base event with core span attributes |
| `NetworkingEvent` | `base/_base.py` | Done | Base for outbound network operations |
| `DatabaseEvent` | `base/database.py` | Done | Database query events |
| `HTTPClientEvent` | `base/http_client.py` | Done | Outbound HTTP request events |
| `HTTPServerEvent` | `base/http_server.py` | Done | Inbound HTTP request events |
| `MessagingEvent` | `contrib/messaging.py` | Done | Base messaging events |
| `KafkaEvent` | `contrib/messaging.py` | Done | Kafka-specific events |

#### Handlers (`ddtrace/_trace/integrations/handlers/`)

| Handler | Location | Status | Description |
|---------|----------|--------|-------------|
| `SpanEventHandler[E]` | `base/_base.py` | Done | Base handler with Generic TypeVar |
| `DatabaseEventHandler` | `base/database.py` | Done | DBM injection, peer service |
| `HTTPClientEventHandler` | `base/http_client.py` | Done | Header injection, peer service |
| `HTTPServerEventHandler` | `base/http_server.py` | Done | Context extraction (parent-child) |
| `MessagingEventHandler[M]` | `base/messaging.py` | Done | DT injection/extraction, peer service |
| `KafkaEventHandler` | `contrib/kafka.py` | Done | DSM checkpoint handling |

#### Instrumentation (`ddtrace/contrib/v2/`)

| Class | Location | Status | Description |
|-------|----------|--------|-------------|
| `InstrumentationPlugin` | `_base.py` | Done | Base class for all instrumentations |
| `DatabaseInstrumentation` | `database.py` | Done | Database integration base |
| `HTTPClientInstrumentation` | `http_client.py` | Done | HTTP client integration base |
| `WebFrameworkInstrumentation` | `http_server.py` | Done | Web framework integration base |
| `MessagingInstrumentation` | `messaging.py` | Done | Messaging integration base |
| `AsyncpgInstrumentation` | `contrib/asyncpg.py` | Done | asyncpg example implementation |

---

## TODO

### 1. Core Event System Integration

**Priority: High**

Wire handlers into `ddtrace.internal.core` dispatch system to enable products (AppSec, LLMObs, Debugging) to hook into integrations.

#### Tasks

- [ ] Add `core.dispatch` calls in `SpanEventHandler.trace()`:
  ```python
  # On span start
  core.dispatch("integration.span_start", (span, event))

  # On span finish
  core.dispatch("integration.span_finish", (span, event))
  ```

- [ ] Add category-specific dispatch events:
  ```python
  # Database
  core.dispatch("integration.database.query", (span, event))

  # HTTP Server
  core.dispatch("integration.http_server.request", (span, event))

  # HTTP Client
  core.dispatch("integration.http_client.request", (span, event))

  # Messaging
  core.dispatch("integration.messaging.produce", (span, event))
  core.dispatch("integration.messaging.consume", (span, event))
  ```

- [ ] Create listener registration in `ddtrace/_trace/integrations/listeners.py`:
  ```python
  def register_integration_listeners():
      """Register core event listeners for product integration."""
      core.on("integration.span_start", _on_span_start)
      core.on("integration.span_finish", _on_span_finish)
      # ... category-specific listeners
  ```

---

### 2. Feature Wiring

**Priority: High**

Ensure all existing dd-trace-py features work with the new framework.

#### Context Propagation

- [ ] HTTP Server: Extract distributed tracing context from headers (Done - parent-child)
- [ ] HTTP Client: Inject distributed tracing context into headers (Done)
- [ ] Messaging Producer: Inject context into message headers (Done)
- [ ] Messaging Consumer: Extract context and link spans (Done)
- [ ] Database: Support DBM context propagation (Partial - need full DBM propagator)

#### Peer Service

- [ ] Verify peer service computation works for all networking events
- [ ] Add `_dd.peer.service.source` tag
- [ ] Support peer service remapping configuration

#### Span Links

- [ ] HTTP Client: Link to parent spans when appropriate
- [ ] Messaging Consumer: Link to producer spans (Done)
- [ ] Add span link support to base handler

#### Resource Naming

- [ ] HTTP Server: Support route-based resource naming
- [ ] Database: Support query normalization
- [ ] Add resource name configuration hooks

#### Service Naming

- [ ] Support schema-based service naming
- [ ] Support service name overrides via config
- [ ] Add `schematize_service_name` integration

---

### 3. Remaining Event Types

**Priority: Medium**

#### Cache Events

- [ ] `CacheEvent` - Base event for cache operations (Redis, Memcached)
  ```python
  @dataclass
  class CacheEvent(NetworkingEvent):
      cache_system: Optional[str] = None  # "redis", "memcached"
      cache_operation: Optional[str] = None  # "get", "set", "delete"
      cache_key: Optional[str] = None
      cache_hit: Optional[bool] = None
  ```

- [ ] `RedisEvent` - Redis-specific fields
- [ ] `MemcachedEvent` - Memcached-specific fields

#### GraphQL Events

- [ ] `GraphQLEvent` - GraphQL operation events
  ```python
  @dataclass
  class GraphQLEvent(SpanEvent):
      graphql_operation_name: Optional[str] = None
      graphql_operation_type: Optional[str] = None  # "query", "mutation", "subscription"
      graphql_document: Optional[str] = None
  ```

#### gRPC Events

- [ ] `GRPCClientEvent` - gRPC client calls
- [ ] `GRPCServerEvent` - gRPC server handlers
  ```python
  @dataclass
  class GRPCClientEvent(NetworkingEvent):
      rpc_system: str = "grpc"
      rpc_service: Optional[str] = None
      rpc_method: Optional[str] = None
      rpc_grpc_status_code: Optional[int] = None
  ```

#### AWS Events

- [ ] `AWSEvent` - Base AWS SDK event
  ```python
  @dataclass
  class AWSEvent(NetworkingEvent):
      aws_service: Optional[str] = None
      aws_operation: Optional[str] = None
      aws_region: Optional[str] = None
      aws_request_id: Optional[str] = None
  ```

- [ ] `S3Event` - S3-specific fields
- [ ] `SQSEvent` - SQS-specific fields (extends MessagingEvent)
- [ ] `SNSEvent` - SNS-specific fields
- [ ] `DynamoDBEvent` - DynamoDB-specific fields

#### Template Events

- [ ] `TemplateEvent` - Template rendering (Jinja2, Mako)
  ```python
  @dataclass
  class TemplateEvent(SpanEvent):
      template_name: Optional[str] = None
      template_engine: Optional[str] = None
  ```

---

### 4. Remaining Handlers

**Priority: Medium**

| Handler | Event Type | Features |
|---------|------------|----------|
| `CacheEventHandler` | `CacheEvent` | Peer service, cache metrics |
| `RedisEventHandler` | `RedisEvent` | Redis-specific tags |
| `GraphQLEventHandler` | `GraphQLEvent` | Operation extraction |
| `GRPCClientEventHandler` | `GRPCClientEvent` | Header injection |
| `GRPCServerEventHandler` | `GRPCServerEvent` | Context extraction |
| `AWSEventHandler` | `AWSEvent` | AWS-specific tagging |
| `TemplateEventHandler` | `TemplateEvent` | Template metrics |

---

### 5. Remaining Instrumentation Classes

**Priority: Medium**

| Class | Base | Description |
|-------|------|-------------|
| `CacheInstrumentation` | `InstrumentationPlugin` | Redis, Memcached base |
| `GraphQLInstrumentation` | `InstrumentationPlugin` | GraphQL libraries |
| `GRPCInstrumentation` | `InstrumentationPlugin` | gRPC client/server |
| `AWSInstrumentation` | `InstrumentationPlugin` | boto3/botocore |
| `TemplateInstrumentation` | `InstrumentationPlugin` | Template engines |

---

### 6. Example Integrations

**Priority: Low**

Implement reference integrations using the new framework:

- [ ] `asyncpg` (Done - in `contrib/v2/contrib/asyncpg.py`)
- [ ] `aiohttp` client
- [ ] `httpx` client
- [ ] `fastapi` / `starlette` server
- [ ] `redis` / `aioredis`
- [ ] `confluent-kafka`

---

### 7. Testing

**Priority: High**

- [ ] Unit tests for all event types
- [ ] Unit tests for all handlers
- [ ] Integration tests for context propagation
- [ ] Integration tests for peer service computation
- [ ] Snapshot tests for span attributes
- [ ] Performance benchmarks vs current integrations

---

### 8. Documentation

**Priority: Medium**

- [ ] Update contributing guide for new integration pattern
- [ ] Add migration guide from old to new pattern
- [ ] Document event type extension patterns
- [ ] Document handler customization patterns

---

## Architecture Decisions

### Why Events + Handlers?

1. **Separation of Concerns**: Instrumentation code focuses on data extraction, handlers focus on span creation
2. **Testability**: Events are plain dataclasses, easy to construct and verify
3. **Extensibility**: New integrations only need to emit events, handlers provide shared logic
4. **Type Safety**: Generic TypeVar pattern ensures type-safe handler inheritance

### Why `ddtrace/contrib/v2/`?

- Allows gradual migration without breaking existing integrations
- Clear separation from legacy integration pattern
- Can be promoted to main `contrib/` once stable

### Core Event Integration Pattern

```
┌─────────────────┐     ┌─────────────┐     ┌─────────────┐
│  Instrumentation │────▶│    Event    │────▶│   Handler   │
│  (patches lib)   │     │ (dataclass) │     │(creates span)│
└─────────────────┘     └─────────────┘     └──────┬──────┘
                                                    │
                                                    ▼
                                            ┌─────────────┐
                                            │core.dispatch│
                                            │  (products) │
                                            └─────────────┘
                                                    │
                              ┌─────────────────────┼─────────────────────┐
                              ▼                     ▼                     ▼
                        ┌─────────┐           ┌─────────┐           ┌─────────┐
                        │ AppSec  │           │ LLMObs  │           │Debugging│
                        └─────────┘           └─────────┘           └─────────┘
```

---

## Timeline

This is a suggested implementation order:

1. **Phase 1**: Core event system integration (enables product hooks)
2. **Phase 2**: Feature wiring verification (ensures parity with existing integrations)
3. **Phase 3**: Remaining event types and handlers (expands coverage)
4. **Phase 4**: Example integrations (proves the pattern)
5. **Phase 5**: Testing and documentation (production readiness)
