# HTTP lifecycle examples

`lifecycle.py` wires Bottle's implemented hooks into the shared WSGI driver.
`asgi_lifecycle.py` wires an unpatched Starlette app into an opt-in ASGI prototype.
The prototype is executable, but does not replace automatic Starlette patching.

## Run the proof

Construct a Starlette app normally, then keep the returned context manager open
while serving its instrumented callable:

```python
with wire_starlette(app, integration_config=config.starlette) as instrumented:
    # Pass instrumented to your ASGI server here.
    ...
```

Exiting the context restores the route hooks, including on failure. Do not combine
this proof with the existing tracing middleware. Duplicate installation on the same
app is rejected. Re-enter after adding routes.

Plain ASGI applications need no framework adapter at all:

```python
driver = AsgiLifecycleDriver(get_config=lambda: settings)
instrumented = driver.wrap(app)
```

The driver extracts standard ASGI values by default. A request_data callback can
override normalization where needed. Starlette preserves its version-specific URL
normalization through a small callback; all other extraction is shared.

`tests/contrib/starlette/test_http_lifecycle.py` exercises real Starlette routes
using the same `HttpContractTests` as Bottle, plus transport-specific cases. Run
it with the repository `scripts/run-tests` runner and a Starlette environment.

## Implemented ownership

- `contrib/internal/http_server.py` supplies shared observations, identity, block
  decisions, and completion. `complete()` publishes the final snapshot;
  `finish()` additionally exits the context in its owning task.
- `contrib/internal/http_asgi.py` owns response-start/body/trailer delivery,
  replacement response framing, scope lookup, failure and cancellation cleanup.
  It extracts request values, wraps selected handlers, and preserves receive
  without eagerly reading request bodies.
- `contrib/internal/starlette/patch.py` identifies routes and mount prefixes and
  installs the shared handler wrappers. There is no Starlette lifecycle class or
  required framework-hooks interface. Request state and events stay in http_server;
  ASGI execution and block enforcement stay in http_asgi.
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
