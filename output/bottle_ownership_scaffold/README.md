# Bottle ownership scaffold

This is an opt-in, executable design example. It does not modify or register production instrumentation.

| File | Owner | Responsibility |
| --- | --- | --- |
| contracts.py | IDM + ASM, with LP transport review | Facts available before the handler and a synchronous blocking response |
| adapter.py | IDM | Extract Bottle facts, dispatch, translate a block into HTTPResponse, otherwise invoke the handler |
| asm_consumer.py | ASM | Inspect framework-independent facts and return a decision |
| Existing core dispatcher/context and span APIs | LP | Dispatch, context activation, span mechanics |
| Existing TracePlugin / WebFrameworkRequestEvent usage | IDM | HTTP span semantics, route naming, status, errors, existing completion boundary |
| Existing span-to-profile bridge | LP + Profiling | Span identity/resource access and profile endpoint attribution; no Bottle-specific profiling callback |

## Try the split

From the repository root, import the scaffold and install it on a Bottle app:

```python
from bottle import Bottle
from output.bottle_ownership_scaffold.adapter import ScaffoldTracePlugin
from output.bottle_ownership_scaffold.asm_consumer import AsmConsumer
from output.bottle_ownership_scaffold.contracts import BlockResponse

app = Bottle()
app.install(ScaffoldTracePlugin())

@app.get("/hello/<name>")
def hello(name):
    return "Hello " + name

def inspect(facts):
    if dict(facts.headers).get("X-Demo-Block") == "yes":
        return BlockResponse()
    return None

# Keep the consumer registered for the application's lifetime.
# The policy above is only a demonstration, not a security control.
with AsmConsumer(inspect):
    app.run(host="127.0.0.1", port=8080)
```

Use this plugin in place of the standard TracePlugin; do not enable Bottle autopatching alongside it.

The outer existing plugin starts its WebFrameworkRequestEvent and span. The scaffold emits BeforeHandlerEvent inside that context. ASM synchronously returns None (continue) or BlockResponse through the event's result field. IDM returns a Bottle HTTPResponse on block, skipping the application callback. The existing outer wrapper records the status and finishes the span.

## Contract deliberately bounded to this stage

- The hook runs after route resolution, before this plugin invokes the callback. Plugin ordering can mean other wrappers still run; this is not an earliest-request interception guarantee.
- Method, URL, copied headers, raw query, route template and copied path parameters are available. Body is explicitly NOT_COLLECTED; no stream is read.
- Parameter values are shallow copies and can include converter-defined objects. Deep immutability, serialization and allowed parameter types need an agreed production contract.
- operation_id is an opaque per-invocation token. Future stage events must carry the same token from a longer-lived operation context; this example has only one stage.
- Dispatch must complete synchronously. There is one startup-registered decision authority. No delayed decisions, multiple-consumer arbitration, transformations or cancellation protocol is implemented.
- Consumer exceptions deliberately propagate in this prototype (Bottle turns ordinary exceptions into 500 responses). This exposes contract failures during design; production failure isolation and fail-open/fail-closed policy remain to be agreed.
- The adapter preserves the existing tracing activation gate. Independent product activation is not demonstrated.
- No real WAF, AppSec context setup, IAST instrumentation, body inspection, duplicate-header preservation or block-response validation is implemented.

## Completion and profiling

This keeps the current callback-return span boundary. It does not observe response commitment, stream iteration/exhaustion, disconnects, or unmatched routes. It also retains existing response-header capture behavior, which reads Bottle's local response rather than headers on a returned HTTPResponse.

A separate WSGI lifecycle adapter would be needed for those stages, sharing a single operation/span with this route hook. Completion must finalize the route/resource before the shared span completion notification. Profiling consumes that shared span contract; it should not subscribe to Bottle-specific callbacks.

For production, move agreed event contracts into contrib/_events/http.py (or a sibling server module), integrate the adapter into the Bottle plugin, and register the real consumer from the AppSec product lifecycle. The current prototype imports no AppSec product code from the adapter and does not introduce a dependency from core onto contrib.

## Validation

The tests exercise real Bottle requests with WebTest and the repository test tracer: allow, block, handler suppression, listener cleanup, inspector failure, one-span behavior, and HTTPError propagation.

Run through the repository runner (never direct pytest):

```sh
scripts/run-tests --list tests/contrib/bottle/test.py
scripts/run-tests --venv 14d8170 -- output/bottle_ownership_scaffold/test_scaffold.py -k OwnershipScaffold
```

The venv hash is from this checkout's discovered Bottle Python 3.14 environment; rediscover if it changes.
