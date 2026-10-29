Flask subscriber lifecycle
=========================

This implementation builds on the product lifecycle hooks and lazy
``SubscriberBundle`` API. Flask uses ``SpanLifecycleSubscriber``, following the
shared span lifecycle model, with integration-owned response completion.
It does not require a shared WSGI lifecycle runner or the Bottle lifecycle branch.

Ownership
---------

* ``ddtrace/contrib/internal/flask/request.py`` extracts request metadata and opens
  a typed ``WebFrameworkRequestEvent`` context named ``flask.request``. Response
  status and headers are recorded after the server accepts them.
* ``ddtrace/contrib/internal/flask/_streaming.py`` observes response iteration,
  exhaustion, errors, and close. It dispatches the deferred context-ended event
  once. It does not create, mutate, or finish spans.
* ``ddtrace/_trace/subscribers/flask.py`` extends ``SpanLifecycleSubscriber`` and
  retains Flask's existing HTTP metadata callbacks. It owns the root span and
  finishes the existing response child before finishing the root. Stream and
  close errors are attributed to the response child.
* ``ddtrace/_trace/_registration.py`` declares the Flask subscriber bundle.
  Tracing's product hook and manual subscriber setup register the same bundle.
  No further product-manager protocol extension is needed.

Span lifecycle hooks
--------------------

The shared subscriber dispatches these events with the request context as their
single argument:

* ``span.starting.flask.request`` runs before span creation. Product subscribers
  can modify the typed event before tracing consumes it.
* ``span.started.flask.request`` runs after span creation and the tracing
  subscriber's start handlers.
* ``span.finishing.flask.request`` runs before root end handlers and finishing.
  Product subscribers can add event tags before the root end handler reads them.

These hooks describe span lifetime. They do not detect WSGI response completion
or replace request/body/response inspection events. Flask's existing security
callbacks remain in place; migrating them to product subscribers is separate work.

Deferred response completion
----------------------------

The application runs inside ``core.context_with_event(...,
dispatch_end_event=False)``. Leaving the lexical context restores its caller
without finishing the span or clearing the typed event. The response adapter
uses the same isolated Context as application execution for each iterator/close
call. Reusing that exact Context preserves Flask request-context reset tokens
created before streaming starts. Active spans do not leak into the WSGI server
between calls. The lexical core context has ended during streaming, as in the
legacy WSGI path; lifecycle hooks receive the retained request context explicitly.

Exhaustion, explicit close, or iteration/close failure calls
``ctx.dispatch_ended_event()``. That notification reaches the lifecycle subscriber,
which emits the finishing hook and finishes the span. Application errors dispatch
completion immediately, since no response adapter can take ownership.

.. literalinclude:: _drafts/flask_subscriber_lifecycle.py
   :language: python

Compatibility boundaries
------------------------

Flask retains its application, response, view, and hook spans. The generic WSGI
path retains its existing root and iterable owners. Flask bypasses legacy root
finishing and the legacy traced iterable to avoid two owners for one request.

Early blocking happens before Flask applies raw-path resource naming. OTel
resource enrichment retains its existing timing after request preparation. HTTP
metadata is not reapplied at completion, preserving custom resources, raw URLs,
and duplicate-header handling from the existing callbacks. Route
metadata is updated while Flask's request proxy is available, and user resource
replacements retain their existing protection.

AppSec and IAST retain their existing events, payloads, and application-return
cleanup boundary. That cleanup notification does not finish or clear the typed
tracing event. Response completion can occur later. The tracing unregister hook
retains listeners for active requests and manual instrumentation; safe removal
would require draining those contexts first.

Validation
----------

``tests/contrib/flask/test_lifecycle.py`` exercises lifecycle hook ordering,
streamed child parenting, caller-context restoration, interleaved requests,
exhaustion, early close, application/iteration/close failures, application
``StopIteration``, and WSGI ``exc_info`` / write-callable forwarding.

Run the Flask and generic WSGI suites with environments discovered through
``scripts/run-tests --list``. Existing Flask resource and AppSec snapshots must
remain unchanged. Full security/version-matrix coverage and request/streaming
performance comparisons remain required before declaring the migration complete.
