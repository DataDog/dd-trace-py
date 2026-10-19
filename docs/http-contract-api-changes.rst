HTTP integration contract API changes
=====================================

This is the implementation record for the experimental Bottle HTTP contract.
Revision numbers identify design changes in this record, not released package or
wire-protocol versions. Update this file whenever signatures, availability, timing,
failure behavior, or ownership change. These APIs are internal; existing framework
tracing events remain compatible. The shared types live in
``ddtrace.contrib._events.http`` and Bottle's adapter lives in
``ddtrace.contrib.internal.bottle.trace``.

Revision 6: block preservation and independent handler checks
-------------------------------------------------------------

Bottle retains an accepted blocking response through application hooks and enforces
its status, headers, and body at the first delivery attempt. A failed delivery may
still trigger Bottle's error-response retry without replaying the blocking body.

The before-handler observation and block check now wrap the resolved callback's
complete plugin chain, independently of trace-plugin selection. Routes that skip
plugins still publish handler activity and allow blocking with tracing disabled.
Route cache resets preserve this boundary. Route-local tracing without an
application-installed plugin retains its existing callback-scoped behavior.

Revision 5: synchronous block checks
-----------------------------------

``HttpRequestBlockCheckEvent`` replaces ``HttpRequestInspectionEvent``. A listener
raises ``HttpRequestBlock(status_code=403, headers=(), body=b"")`` to interrupt a
check. The exception derives from ``BaseException`` and uses existing hub propagation;
ordinary listener exceptions retain their existing isolation behavior. The first
block skips remaining listeners. Bottle validates the response before enforcing it;
an invalid block is ignored without resuming skipped listeners.

Observation events remain separate and read-only by convention. Checks follow the
first four lifecycle observations, only before commitment, and stop after a valid
block. Response delivery, stream closure, HEAD suppression, and completion remain
integration-owned. Removed: ``submit``, acceptance results, inspection decisions,
enforcement capability sets, and post-commit termination. There is no retained or
asynchronous decision channel. See ``examples/http-events/contract.py`` for the
current consumer API. Earlier revisions below are historical.

Revision 4: direct fields and phase-specific observations
-------------------------------------------------------

Consumers subscribe to phase-specific data event classes and read fields directly:
``event.request``, ``event.response``, and ``event.request_id``. The shared
``HttpRequestDataEvent`` is a field base, not a subscription. ``HttpRequestSnapshot``
and ``event.snapshot`` have been removed. Event envelopes follow the existing mutable
Event API but must be treated as read-only; nested HTTP values remain frozen.
Each dispatch creates a new envelope. Inspection exposes the same fields plus
``submit``. The adapter captures capabilities independently of the event, so changing
an event cannot grant additional enforcement actions. See
``examples/http-events/contract.py`` for the current consumer example. Earlier
revisions below document the historical API.

Revision 3: synchronous inspection and framework enforcement
-----------------------------------------------------------

Status: implemented in Bottle; no production WAF subscriber is registered by this
change. Tests exercise the consumer boundary with inspection subscribers. ASM owns
WAF evaluation, policy, evaluation time budgets, and selection of blocking responses.
IDM owns capability reporting, response validation, and framework enforcement. LP
continues to provide the existing event dispatch and context mechanisms.

Added interfaces
~~~~~~~~~~~~~~~~

* ``HttpBlockingResponse(status_code=403, headers=(), body=b"")``: frozen replacement
  response. Headers are a tuple of name/value pairs and body is bytes.
* ``HttpInspectionDecision(action=None, response=None)``: frozen decision.
  ``action=None`` allows processing to continue. ``RESPOND`` requires a
  ``HttpBlockingResponse``. ``TERMINATE`` requires no response.
* ``HttpDecisionAcceptance``: ``ACCEPTED``, ``CLOSED``, ``INVALID``, ``UNSUPPORTED``,
  and ``ALREADY_DECIDED``.
* ``HttpRequestInspectionEvent(snapshot, submit)``: dispatched as
  ``http.server.request.inspect``. Register using ``core.on``. The snapshot uses
  the same immutable types as the observational data event. Call
  ``submit(HttpInspectionDecision(...))`` to receive a ``HttpDecisionAcceptance``.
  Listener return values are ignored. Do not modify the event envelope.

Inspection follows the observational data event at each boundary except completion.
The capability set describes the inspection immediately following that observation:

.. list-table:: Bottle capabilities
   :header-rows: 1

   * - Phase
     - Supported actions
     - Enforcement
   * - REQUEST_STARTED
     - RESPOND
     - Skip the Bottle application, including hooks and routing.
   * - ROUTE_RESOLVED
     - RESPOND
     - Raise a Bottle HTTPResponse before the route callback.
   * - BEFORE_HANDLER
     - RESPOND
     - Return a Bottle HTTPResponse instead of invoking the wrapped callback.
   * - RESPONSE_READY
     - RESPOND
     - Replace status, headers, and body before server start_response.
   * - RESPONSE_COMMITTED
     - TERMINATE
     - Close the original iterable and produce no further body bytes.
   * - COMPLETED
     - None
     - No inspection dispatch; the request has ended.

The first accepted enforcement decision at each checkpoint wins. Allow is a no-op
and cannot cancel an accepted block. Later checkpoints are independent. Replacement
responses do not generate an extra response-ready inspection, avoiding recursive
inspection of the enforcement response. Committed and completed observations carry
the final status and headers. A block is normal HTTP control flow, not an exception
recorded on the span. Existing application failures remain recorded.

Acceptance is not a delivery acknowledgement. Server failures and iterable-close
failures still follow the normal request error/completion path. Replacing a streamed
response closes the original iterable without advancing it further. Bottle may
already have consumed its first chunk when preparing the response. Termination
preserves committed status and headers; WSGI provides no portable socket-abort API,
so termination means stopping the iterable, not guaranteeing a TCP connection reset.
HEAD replacement responses have no body. IDM owns Content-Length framing. Response
codes must be integers from 200 through 599; 204, 205, and 304 require empty bodies.
Header names must be HTTP tokens and values must be Latin-1 without control
characters. Content-Length and hop-by-hop transport headers are rejected.

Failure and timing contract
~~~~~~~~~~~~~~~~~~~~~~~~~~~

``submit`` is valid only on the dispatching thread and only until synchronous
inspection dispatch returns. Calls from another thread or retained callbacks return
``CLOSED`` without changing the request. Asynchronous inspection is not supported.
The dispatcher does not preempt a slow subscriber: the security evaluator must
apply its own time budget and return or raise on timeout.

Ordinary listener exceptions, including TimeoutError, use core's production failure
isolation. The existing DD_TESTING_RAISE diagnostic setting deliberately re-raises
listener failures during development.
Absent an accepted enforcement decision, processing continues. Invalid or unsupported
decisions also leave processing unchanged. A valid decision submitted before a
listener raises remains accepted. Process-control BaseExceptions still propagate;
they are not converted into security decisions. There is no fail-closed mode in this
revision. No listener means no inspection work. An inspection listener keeps the
lifecycle active when tracing is disabled, without creating a request span.

Consumer example (policy belongs to the subscriber)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

    from ddtrace.contrib._events.http import HttpBlockingResponse
    from ddtrace.contrib._events.http import HttpEnforcementAction
    from ddtrace.contrib._events.http import HttpInspectionDecision
    from ddtrace.contrib._events.http import HttpRequestInspectionEvent
    from ddtrace.internal import core

    def inspect_request(event):
        snapshot = event.snapshot
        if HttpEnforcementAction.RESPOND not in snapshot.supported_actions:
            return
        # The product evaluates available fields using its own policy and budget.
        if evaluate_policy(snapshot):
            event.submit(HttpInspectionDecision(
                action=HttpEnforcementAction.RESPOND,
                response=HttpBlockingResponse(
                    status_code=403,
                    headers=(("Content-Type", "text/plain"),),
                    body=b"Request blocked",
                ),
            ))

    core.on(HttpRequestInspectionEvent.event_name, inspect_request)

Revision 2: HTTP data independent of tracing
-------------------------------------------

Added frozen ``HttpRequestData``, ``HttpResponseData``, ``HttpRequestError``, and
``HttpRequestSnapshot`` values, ``HttpRequestPhase`` and ``HttpEnforcementAction``
enums, and the ``HttpRequestDataEvent(snapshot)`` envelope named
``http.server.request.data``. The snapshot carries an opaque process-local request
token, framework, phase, HTTP values, error information, lifecycle flags, and
capabilities. None means unavailable; empty values mean available but empty.
Headers/query pairs preserve duplicates. Bodies are not read to fill this contract.
Snapshots may be retained after dispatch. This event is observational only.

The tracing-independent data path can run without a request span. Existing
``HttpBaseEvent``, ``HttpRequestBaseEvent``, and ``WebFrameworkRequestEvent`` remain
compatible. Revision 2 advertised no actions; revision 3 populates capabilities.
Detailed data availability and phase timing are documented in
:doc:`contributing-integrations`.

Revision 1: IDM-owned request lifecycle
--------------------------------------

The application-installed Bottle plugin owns entry, routing, wrapped handler
invocation, response status/errors, streaming, and completion. It finalizes the
resource before completing the request span. Shared context/span mechanisms remain
in LP; route/resource meaning and completion decisions remain in IDM. Route-local
plugins retain their legacy callback scope and do not expose the new contract.

Outstanding boundaries
----------------------

* Register an ASM-owned subscriber and map snapshots to WAF inputs and results,
  including its timeout, telemetry, and product enable/disable lifecycle.
* Expose additional data only when available without consuming application streams.
* Agree the LP/Profiling completed local-root span identity/resource contract.
* Migrate other framework adapters independently; these events are currently
  emitted by the application-installed Bottle plugin only.
