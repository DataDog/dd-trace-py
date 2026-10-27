Flask request consolidation draft
=================================

Status: design scaffold only. The running integration remains the implementation
on ``quinna/flask-request-boundary``. The Python example below is not imported by
patching, and its subscriber has automatic registration disabled. The proposed
``run_in_scope`` runner argument does not exist yet. This is not a completed
migration or evidence of tracing/security parity.

Target ownership
----------------

Use one typed request context to create and finish the root request span through
shared tracing subscribers. ``WSGIResponseIterable`` owns that context from
application entry through response exhaustion, error, or close. It already
provides context isolation and close-once behavior; do not create a second owner.

Keep Flask's application, response, view, and hook spans. The existing application
executor can temporarily supply these child spans and the legacy security
callbacks. Preserving those spans does not require retaining a second request
span or a second context lifecycle.

At application return, emit the existing ``context.ended.wsgi.__call__`` security
notification with the typed context's compatibility data. Do not call
``dispatch_ended_event`` on that context: doing so would finish its request span
and clear the event before streaming completes. The legacy AppSec/IAST handlers
consume this notification at the same boundary as today.

Use a distinct ``flask.request`` context route because its tracing lifetime and
security cleanup boundary differ. Register the Flask tracing specialization on
that route when activating the implementation. AppSec/IAST continue subscribing
to their existing legacy cleanup event; do not also register their generic
end-of-request subscriber on ``flask.request``. Otherwise cleanup would run again
at response completion. Keep shared blocking inspection inactive during this
migration so existing Flask security callbacks remain the sole evaluation path.

The runner extension
--------------------

Add an optional ``run_in_scope`` callback to ``run_web_framework_request``. The
runner still owns preparation, the invocation closure, safe metadata completion,
and the optional execution adapter. It creates the context once. With no scope
callback, it retains the existing synchronous ``with scope: run()`` behavior.
With a scope callback, it returns ``run_in_scope(scope, run)`` instead.

The deferred callback owns entering and exiting the supplied scope exactly once.
Preparation failure must retain the runner's existing bypass behavior. Context
entry failures must not invoke the application. Do not change Bottle's default
execution or import Flask/WSGI implementation code into the shared runner.

Proposed boundary and subscriber
--------------------------------

The following scaffold illustrates those interfaces using the existing WSGI
owner. It deliberately takes the proposed runner as a dependency instead of
calling today's incompatible runner signature. It is not enabled by importing
Flask or calling ``patch()``.

.. literalinclude:: _drafts/flask_request_consolidation.py
   :language: python

Deletion and ownership map
--------------------------

Perform each deletion together with its replacement and compatibility tests.
Do not activate the scaffold alongside the existing deferred request scope.

* ``flask/patch.py``: replace inherited request-context creation with the shared
  runner boundary. Remove duplicate metadata preparation from
  ``_request_call_modifier``. Keep the body-extraction dispatch, view security
  callbacks, response inspection, and patch/unpatch symmetry.
* ``wsgi/wsgi.py``: remove the Flask-only deferred branch in ``__call__``, including
  its local ``request_scope`` and ``invoke`` closures and
  ``wsgi.response.complete`` dispatch. Generic WSGI retains its existing request
  context and iterable wrapper. The application executor remains shared.
* ``trace_handlers.py``: remove ``_on_wsgi_response_complete`` and its registration.
  The typed subscriber finishes the request; the Flask specialization finishes
  the response. Retain child-span callbacks and their existing error semantics.
  The temporary deferred flag still prevents those callbacks from finishing the
  request or returning a second iterable wrapper; remove it only when those child
  spans also have a replacement owner.
* ``trace_handlers.py``: remove Flask's request-start modifier and registration
  once version/measured tags, propagation, initial URL/IP metadata, and initial
  resource behavior come from the typed event/subscriber. Do not delete generic
  WSGI's root-span registration; other integrations still use it.
* ``trace_handlers.py``: reduce ``_on_start_response_pre`` to behavior that the
  shared metadata subscriber does not yet cover. Transfer final HTTP tags and
  resource fallback to the shared subscriber. Preserve route enrichment before
  the view, application-span route tags, numeric view-argument metrics, and
  user-supplied resource overrides before removing the related helpers.
* ``appsec/_contrib/flask/__init__.py``: take ownership of request-body/query/cookie
  and response metadata currently forwarded through tracing-only Flask handlers.
  Preserve the existing event names, payloads, ordering, normalized-route updates,
  and pre-commitment blocking. Audit response cookies before deleting
  ``_cookies_from_response_headers``. Move this behavior only after equivalent
  security coverage passes; do not merely remove the tracing registrations.

The migration is complete when the typed event is the root tracing input, the
shared subscriber is the sole root-span owner, and the old request setup and
completion branches have been deleted. Moving unchanged handlers to another file
or adding more inactive production paths is not that outcome.

Checks required before activation
---------------------------------

These are pending acceptance checks, not tests implemented by this scaffold.

* Shared runner: existing synchronous behavior, preparation failure invoking the
  callback once, deferred completion, context-entry failure, and exception cleanup.
* Flask tracing: exactly one request span; unchanged application/response hierarchy;
  distributed propagation and inferred spans; schema variants; resource overrides;
  before-view route enrichment; numeric view arguments; final status and headers;
  automatic OPTIONS, early returns, unmatched routes, and handled/unhandled errors.
* WSGI lifetime: interleaved streams, caller context restoration, lazy iteration,
  early close, exhaustion, application/iteration/close errors, process-control
  exceptions, close-once semantics, and forwarding ``start_response``'s
  ``exc_info`` and write callable. Generic WSGI must retain its behavior.
* AppSec/IAST: one cleanup notification at application return; blocking before and
  after views; request bodies, cookies, path arguments, API Security metadata,
  taint propagation and reports; no second shared inspection or cleanup path.
* Regression: existing Flask, WSGI, Bottle/shared-runner, and dedicated Flask
  security suites through ``scripts/run-tests``; formatting, typing, full lint,
  and import/dependency comparisons through the repository tools.
* Performance: compare the consolidated implementation with both the original
  Bottle base and the current Flask branch. The previous 1,000-chunk benchmark
  measured about 23% slower traced execution, with sample variability and no local
  agent. Recheck streaming and ordinary requests before treating that cost as
  accepted or resolved. This documentation-only scaffold does not change it.
