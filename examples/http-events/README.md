# HTTP lifecycle examples

`lifecycle.py` wires Bottle's implemented hooks into the shared WSGI driver.
`asgi_lifecycle.py` wires an unpatched Starlette app into an opt-in ASGI prototype.
The prototype is executable, but does not replace automatic Starlette patching.

## Run the proof

Construct a Starlette app normally, then serve the callable returned by
`wire_starlette(app, integration_config=config.starlette)` after initializing
Starlette integration configuration. Call its `close()` method to restore the route hooks when
finished. Do not combine this wrapper with the existing tracing middleware or
install it twice. Recreate the wrapper after adding routes.

`tests/contrib/starlette/test_http_lifecycle.py` exercises real Starlette routes
using the same `HttpContractTests` as Bottle, plus transport-specific cases. Run
it with the repository `scripts/run-tests` runner and a Starlette environment.

## Implemented ownership

- `contrib/internal/http_server.py` supplies shared observations, identity, block
  decisions, and completion. `complete()` publishes the final snapshot;
  `finish()` additionally exits the context in its owning task.
- `contrib/internal/http_asgi.py` owns response-start/body/trailer delivery,
  replacement response framing, scope lookup, failure and cancellation cleanup.
  It preserves receive without eagerly reading request bodies.
- `contrib/internal/starlette/lifecycle.py` wraps selected routes, including mount
  prefixes, and emits route and pre-handler checks. It restores hooks on close.
- The shared tracing subscriber finalizes metadata and, when the request context
  opts into `finish_on_response`, finishes successful spans at response completion.
  Owner-task cleanup still occurs later. Error responses wait for app unwind so
  exception details are available. Bottle retains its existing context-end path.

No listener returns request-control values. HTTP observations are push-only;
synchronous block-check listeners raise the shared block exception. The driver
turns an accepted block into a response and an internal unwind signal. Task-group
exceptions containing only that signal are consumed; mixed failures propagate.

Successful terminal body delivery completes the response, or final trailers do
when announced. Context exit stays in the owner task even if sends run in a child
task. Post-response failures propagate without rewriting the completed request.

## Prototype limits

This proves the contract and transport separation, not full integration parity.
There is one outer lifecycle for mounted routes; nested driver installation is
rejected. Nested application spans, FastAPI shared patch coordination, host-based
routing, dynamic route changes, middleware short-circuit fallback attribution,
custom span callbacks/names, version tags, and configurable 404 behavior still
need migration work. WebSocket and lifespan scopes pass through without tracing.
Background-task span creation/linking is still owned by existing instrumentation;
this proof only verifies exclusion from successful request duration.

Request body parsing/replay, additional security data, production WAF wiring, and
IAST compatibility are not implemented. Disconnect messages are passed through;
completion follows application termination rather than treating a received
message as proof that execution has stopped. ASGI response extensions other than
body/trailers need separate policies before production use.
