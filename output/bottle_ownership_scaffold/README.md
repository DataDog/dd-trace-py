# Bottle ownership POC

The POC now modifies the actual Bottle integration. Install the standard TracePlugin,
or use the existing Bottle autopatching. The separate scaffold adapter and contracts
have been removed.

| Code | Owner | Responsibility |
| --- | --- | --- |
| ddtrace/contrib/internal/bottle/trace.py | IDM | Extract request facts, emit the pre-handler stage, enforce a response decision through Bottle |
| ddtrace/contrib/_events/http.py | IDM + ASM, with LP transport review | Stage fields and synchronous response contract |
| ddtrace/internal/core | LP | Existing dispatch and active context mechanisms, unchanged by this POC |
| asm_consumer.py in this directory | Sample ASM boundary | Explicitly registered inspection policy; no real WAF integration |
| tests/contrib/bottle/test_lifecycle.py | Integration contract tests | Exercise the actual TracePlugin with test consumers |

## Flow

The existing WebFrameworkRequestEvent opens the request span. If a consumer is
registered, Bottle emits HttpServerBeforeHandlerEvent inside that context.
A consumer may set block_response to HttpServerBlockResponse; otherwise it stays
None and the handler runs. IDM translates a decision into Bottle's HTTPResponse,
skips the handler, and records the blocked status and response headers.

No new LP runtime code or profiling callbacks are introduced. Existing span
semantics and completion remain in place.

## Example consumer

The sample policy remains outside production product registration:

```python
from bottle import Bottle
from ddtrace.contrib.internal.bottle.patch import TracePlugin
from ddtrace.contrib._events.http import HttpServerBlockResponse
from output.bottle_ownership_scaffold.asm_consumer import AsmConsumer

app = Bottle()
app.install(TracePlugin())

@app.get("/hello/<name>")
def hello(name):
    return "Hello " + name

def inspect(facts):
    if facts.path_parameters == (("name", "blocked"),):
        return HttpServerBlockResponse()
    return None

with AsmConsumer(inspect):
    app.run(host="127.0.0.1", port=8080)
```

Install the plugin manually or use autopatching, not both.

## Boundaries still under discussion

This is a POC, not released Bottle WAF support. There is no production ASM
subscriber, body inspection, or IAST wiring. The sample consumer demonstrates the
ownership boundary without importing product code from contrib.

Inspection is synchronous, with a single decision authority. Consumer exceptions
propagate and Bottle converts ordinary exceptions to HTTP 500. Production failure
isolation, fail-open/fail-closed behavior, decision arbitration, and validation of
block response specifications still require an agreed contract.

Headers and path parameters are copied; converter-defined parameter objects are
shallow copies. Body availability is explicitly NOT_COLLECTED and the request body
is not read. Duplicate request headers are not preserved.

The existing activation gate is retained. This does not demonstrate independent
ASM activation. The event runs after routing and before the wrapped callback;
plugin order can put other wrappers around it. Unmatched routes are not observed.

operation_id is an opaque token for this invocation. Future stage events would
need to carry the same token through a longer-lived operation context.

The span still ends when the callback returns. Streaming completion, disconnects,
response commitment and early request interception need a WSGI lifecycle adapter
that shares this request's operation/span. Profiling continues to consume the
shared span bridge, with no Bottle-specific consumer.

## Validation

Run the entire Bottle suite, including the lifecycle tests, using an environment
listed by the repository runner:

```sh
scripts/run-tests --list tests/contrib/bottle/test.py
scripts/run-tests --venv 14d8170
```

The listed hash selects Python 3.14 with current Bottle. The 15dee3b environment
covers Python 3.9 and Bottle 0.12.
