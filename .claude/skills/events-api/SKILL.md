---
name: events-api
description: >
  Design, implement, migrate, or review dd-trace-py typed events and subscribers.
  Use for core.context_with_event, core.dispatch_event, event contracts,
  Subscriber/ContextSubscriber/TracingSubscriber composition, registration,
  or moving context_with_data and trace_handlers behavior into shared subscribers.
---

# Events and Subscribers

The events API supports modularizing ddtrace: integrations expose operations and
their data; subscribers implement product behavior. Eventually integrations should
contain no tracing code. The API replaces implicit, untyped context_with_data
contracts, an ever-growing trace_handlers.py, and duplicated handlers for equivalent
operations across integrations.

## Ownership and placement

- An event is a typed transport. Keep operations in an event to a minimum. Small
  amounts of data preparation are acceptable for consistency; creating spans,
  setting span tags, security decisions, and other product behavior belong in
  subscribers.
- Place events close to their dispatching source. Integration events belong under
  ddtrace/contrib/, commonly ddtrace/contrib/_events/. Product-owned base events
  can live in their product, such as ddtrace/_trace/events.py.
- Place each subscriber in the product it represents. Tracing subscribers live
  under ddtrace/_trace/subscribers/; other products own their subscribers.
- Integrations extract library data, expose library-specific actions when needed,
  call the wrapped operation, and populate results. Product subscribers consume
  that contract. Product code should not import the instrumented library.
- Existing code is in transition. Do not copy direct span manipulation or product
  imports from an integration as the intended architecture. Product agnosticism
  describes code ownership; it does not establish a guarantee that an operation
  works without its subscribers.

## Reuse before adding types

Search ddtrace/contrib/_events/ and the relevant product's subscribers before
adding an event or handler. When operations across integrations have the same
semantics, share their event contract and product behavior. Differences in library
APIs alone do not require separate events or subscribers.

Aim for as few events and subscribers as the semantics allow. Both are composable:
extend an existing event with additional typed data, and inherit a subscriber to
extend its behavior. A subscriber can also listen to multiple event names when
their contracts support the same behavior. Event inheritance alone does not
subscribe handlers to the child's event name; routing uses event_names.

Integration-specific events are fine when needed. Avoid forcing unrelated
operations into one event with many unrelated optional fields, or one subscriber
that branches repeatedly on integration names. Keep shared behavior in reusable
bases and add only the required specialization.

The API is still under construction. If implementing a legitimate operation
requires excessive workarounds, consider a focused API improvement and explain
the gap. Preserve compatibility and follow the repository's architecture rules.

## Define an explicit contract

The event definition should tell a dispatcher what data to supply without reading
subscribers. Use meaningful types and names, defaults for genuinely optional data,
and short documentation for non-obvious semantics. Specify which fields are
available at start and which are populated before completion.

- Decorate Event subclasses with @dataclass. Define event_name on the class.
- For fields added to context event dataclasses, use event_field(), including
  defaults and default_factory. It handles keyword-only inheritance across
  supported Python versions.
- Use InitVar with event_field() only for construction inputs that should not be
  retained. Keep any __post_init__ processing minimal.
- Access declared operation data through ctx.event. Keep product execution state
  in the owning product/context rather than adding it to the transport or
  recreating the event contract in string-keyed context items.
- Preserve the concrete type with ExecutionContext[MyEvent] and
  ContextSubscriber[MyEvent] where applicable.

Choose core.dispatch_event(event) for a point-in-time notification, paired with
Subscriber.on_event(event). Choose core.context_with_event(event) for an operation
with start/end behavior, paired with ContextSubscriber.on_started(ctx) and
on_ended(ctx, exc_info). Events are dispatched synchronously.

For tracing, reuse TracingEvent and TracingSubscriber where their contracts fit.
TracingSubscriber creates the span before start hooks and finishes it after end
hooks. Specialized tracing hooks should add their behavior without duplicating
span lifecycle management. TracingEvent.create() is available for operations that
need only the existing generic tracing contract.

## Routing and context name overrides

By default, core.context_with_event(event) dispatches
context.started.<event.event_name> and context.ended.<event.event_name>.
A ContextSubscriber declares the unprefixed name in event_names; registration
adds the lifecycle prefixes automatically.

Use context_name_override when the same event data contract needs a different
dispatch route. This allows specialized subscribers without defining a new event
class solely to change its name:

```python
with core.context_with_event(
    OperationEvent(operation="fetch"),
    context_name_override="example.specialized_operation",
) as ctx:
    ...
```

This dispatches context.started.example.specialized_operation and
context.ended.example.specialized_operation. The event's event_name and concrete
type are unchanged; ctx.identifier holds the override. Only the selected route
is dispatched, so subscribers registered solely for the original name will not
run. A shared subscriber can include both names in event_names; a specialized
subscriber can inherit shared hooks and declare the specialized name.

Keep the payload compatible with every subscriber on the selected route. Prefer
the existing route when its behavior already fits. The override applies to context
lifecycle dispatch, including deferred completion; core.dispatch_event(event)
still routes by event.event_name.

## Registration and composition

Subscribers automatically register when their module is imported and their class
is defined. A class must define its own event_names to register; merely inheriting
event_names does not register a specialization. Shared bases can omit event_names.

Wire the module into the owning product's import/initialization path. An unimported
subscriber never registers. Do not import product subscribers from integration
wrappers to force registration. Products that deliberately gate registration can
set auto_register = False and call register()/unregister() through their lifecycle.
See ddtrace/_trace/subscribers/__init__.py for import-based registration.

Subscriber inheritance automatically collects each class's own on_event,
on_started, and on_ended hooks. Hooks execute parent-first, including end hooks.
Do not call super().on_event(), super().on_started(), or super().on_ended() to
chain them: this duplicates behavior. Override the public hooks for ordinary
specializations; the internal _on_* methods implement dispatch orchestration.

## Lifecycle and results

With core.context_with_event(event), start hooks run on entry; end hooks receive
the context and (exception_type, exception_value, traceback) on exit. Populate
results before the end dispatch. The event remains available throughout the end
hooks, then the context releases its event reference. Do not read ctx.event after
completion or retain large event payloads unnecessarily.

The following illustrative modules show the boundary; reuse an existing semantic
event in real code when one fits.

```python
# ddtrace/contrib/_events/example.py
from dataclasses import dataclass
from typing import Optional

from ddtrace.internal.core.events import Event, event_field


@dataclass
class OperationEvent(Event):
    event_name = "example.operation"

    operation: str = event_field()
    result_size: Optional[int] = event_field(default=None)
```

```python
# Integration wrapper: extract inputs and populate results before completion.
from ddtrace.contrib._events.example import OperationEvent
from ddtrace.internal import core


def wrapped_operation(wrapped, args, kwargs):
    with core.context_with_event(OperationEvent(operation="fetch")) as ctx:
        result = wrapped(*args, **kwargs)
        ctx.event.result_size = len(result)
        return result
```

A product's ContextSubscriber[OperationEvent] declares
event_names = (OperationEvent.event_name,) and handles the data in its hooks.
The wrapper contains no product behavior; the event only declares data.

## Asynchronous and deferred completion

For background tasks, streaming, callbacks, or other deferred operations, the with
block may cover only setup. Use core.context_with_event(event,
dispatch_end_event=False) to run start hooks immediately and defer end dispatch.
Pass the returned ctx to the callback, task, or stream owner that observes actual
completion. After populating final event data, that owner calls
ctx.dispatch_ended_event(); on failure, pass the exception type, value, and
traceback, for example ctx.dispatch_ended_event(*sys.exc_info()) inside the
exception handler.

This later dispatch invokes ContextSubscriber.on_ended(ctx, exc_info), including
inherited hooks, on the same context. With TracingSubscriber it also finishes the
span after the end hooks. Hooks still run synchronously when dispatch_ended_event()
is called; deferring completion does not schedule or await asynchronous subscriber
methods.

For example, an asyncio future's completion callback can receive the retained ctx
alongside the future and finalize it as follows. This uses the OperationEvent
contract above and assumes the future returns a sized result:

```python
import sys
from asyncio import Future


def on_completed(ctx: core.ExecutionContext[OperationEvent], future: Future[bytes]) -> None:
    try:
        result = future.result()
        ctx.event.result_size = len(result)
    except BaseException:
        ctx.dispatch_ended_event(*sys.exc_info())
    else:
        ctx.dispatch_ended_event()
```

Here future.result() also surfaces cancellation; the callback reports it to end
hooks without raising it again from the callback. Preserve error propagation
appropriate to the integration: a wrapper awaiting the operation should normally
re-raise after reporting the exception.

Exiting the with block restores the current execution context even when end
dispatch is deferred. Completion hooks should use the ctx they receive rather
than assume core.current still identifies that operation. The retained ctx keeps
its event available until end dispatch finishes, then releases it.

Assign completion ownership clearly. Cover setup failure as well as success,
error, cancellation, and stream close paths as applicable. With automatic end
dispatch disabled, setup errors also need explicit finalization. Repeated calls
after a completed end dispatch are ignored, but finalization code must not access
ctx.event again after completion. An async function whose whole operation is
awaited inside the with block can use normal automatic end dispatch; being async
alone does not require deferral.

## Review and validation

When migrating existing handlers, preserve observable behavior while moving product
logic into its owning subscriber. Check reuse, contract completeness, import
registration, overridden routes, inherited hook order, result availability, and
actual completion.
Avoid copying legacy tracing code into events or adding another equivalent handler
to trace_handlers.py.

Use the repository's run-tests and lint skills for implementation changes. Relevant
tests should exercise product behavior through dispatch, including specialization
without duplicate hooks, routing overrides, and deferred/error completion when
affected. For shared behavior, validate the affected integrations as well as the
subscriber.

Read these sources as needed; existing migrations can contain transitional code:

- API: ddtrace/internal/core/events.py, subscriber.py, and __init__.py.
- Tracing composition: ddtrace/_trace/events.py and subscribers/_base.py.
- Shared contracts and behavior: ddtrace/contrib/_events/http.py,
  http_client.py, and ddtrace/_trace/subscribers/http_client.py.
- Integration dispatch: ddtrace/contrib/internal/requests/connection.py.
- Another product: ddtrace/appsec/_contrib/httpx/subscribers.py.
- Mechanics and lifetime tests: tests/internal/test_subscribers.py and
  tests/internal/events/test_context_events.py.

For broader integration work, also read [apm-integrations](../apm-integrations/SKILL.md).
