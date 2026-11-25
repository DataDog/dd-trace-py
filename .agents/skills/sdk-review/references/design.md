# Design

Where code belongs, who owns state, and API and type shape.

**When planning:** take the rules below into account when designing the change.

**When reviewing:** report a finding when the diff matches a rule's `Flag:` line and no `Not when:` applies, or when it has a clear problem in this lens's area that no rule covers (mark it `no rule`). Cite the rule ID and propose the concrete alternative.

## 1. Product isolation and layering

### DES-1.1: Set span data in tracing subscribers, not in contrib code
- Flag: code under `ddtrace/contrib/internal/` that calls `Span` methods (`span._set_attribute`, `set_tag`, `set_exc_info`) or creates spans, instead of dispatching a core event handled under `ddtrace/_trace/subscribers/`. See [contributing-integrations.rst](../../../../docs/contributing-integrations.rst).
- ❌ `span.set_exc_info(*sys.exc_info())` in `aiokafka/patch.py`
- ✅ `core.dispatch(...)` in the patch, with the error recorded by a subscriber in `ddtrace/_trace/subscribers/`

### DES-1.2: Emit generic events from shared code and let each product register its own listener
- Flag: product- or environment-specific logic (AWS Lambda MicroVM, AppSec, LLMObs) added to each web integration or to a shared dispatcher, or a new per-integration event where the WSGI/ASGI base middleware could emit one. See [isolated-responsibility.mdc](../../../../.cursor/rules/isolated-responsibility.mdc).
- ❌ a MicroVM identity refresh called from the Flask, Django and FastAPI integrations
- ✅ one generic request-starting event from the shared base, with a MicroVM-only listener registered by the Lambda code

### DES-1.3: Let products own enablement and subscriber registration
- Flag: an integration that checks whether tracing is enabled before dispatching, passes a tracing-enabled flag to subscribers, declares or imports tracing subscriber bundles (`requires_subscribers`), or a second registration path alongside product activation.
- ❌ `if config._tracing_enabled: core.dispatch(...)`
- ✅ always dispatch; the tracing product registers and unregisters its own bundles

### DES-1.4: Fix dependency direction instead of adding layer exceptions or moving code to dodge them
- Flag: a new exception in `scripts/import-analysis/layers.json`, contrib importing `ddtrace.llmobs`, new `Span` dependencies in `ddtrace.llmobs` helpers, product code loaded from `ddtrace/contrib/__init__.py`, or a product-specific helper moved into a core module to avoid a violation. Use [dependency-direction-analysis](../../dependency-direction-analysis/SKILL.md).
- ✅ pass product data through `ddtrace.internal.core.ExecutionContext` instead of span or tracer types.

### DES-1.5: Put generic helpers in generic modules, and leave existing helpers where they are
- Flag: a helper with no product-specific logic added under a product package (for example, an exit-signal callback in `ddtrace/internal/datastreams/`), or existing helpers moved to a new module and re-exported from the old one.

## 2. Fix shared components instead of working around them

### DES-2.1: Extend the shared component rather than tearing down and rebuilding around it
- Flag: shutting down and recreating the writer or exporter to change one value (for example, runtime ID), or a caller-side workaround for a limitation in libdatadog or another shared component.
- ✅ add the capability to the libdatadog exporter and call it.

### DES-2.2: Don't add code paths for unsupported or unused inputs
- Flag: handling for custom writers or subclasses the API doesn't support (`inspect.signature` checks on `recreate`), `subclass` on a native `#[pyclass]` that isn't meant to be subclassed, or handling for a config key or suitespec field that nothing uses yet.
- ❌ `if "client" in inspect.signature(writer.recreate).parameters:`
- ✅ one `recreate()` signature on the `TraceWriter` interface

## 3. API shape

### DES-3.1: Keep public annotations to the documented contract
- Flag: a public API annotation widened to match the implementation (for example, the `Span.set_tag` value type), which moves conversion responsibility away from callers.

### DES-3.2: Don't grow the public surface of public modules
- Flag: new non-underscore module attributes, lazy `__getattr__` exports, or `__all__` additions in a public module (for example, `ddtrace.version`) for internal helpers.
- ❌ `distributions = ...` in `ddtrace/version.py`
- ✅ `_distributions = ...`, with `__all__ = ["__version__"]` unchanged

### DES-3.3: Read `Context` internals through accessors, and expose one property per value
- Flag: code outside `Context` reading `_meta`/`_metrics` keys directly, or a native type exposing both `_context` and `context` to Python.
- ❌ `span.context._meta.get(SAMPLING_DECISION_TRACE_TAG_KEY)`
- ✅ `span.context._get_sampling_decision_maker()`

## 4. Change scope

### DES-4.1: Keep changes unrelated to the PR's stated goal out of the PR
- Flag: formatting-only edits, import-style changes, skill or tooling edits, dependency bumps, or fixes that the PR title and description don't cover. See [Pull Request Requirements](../../../../docs/contributing.rst).

### DES-4.2: Separate user-facing fixes and new integrations from refactors
- Flag: a behaviour change or bug fix (often with its own release note) inside a refactor, CI, or tooling PR, or a new integration bundled with an unrelated change.

## 5. Documentation placement

### DES-5.1: Put contributor knowledge in developer docs, not only in agent files
- Flag: architecture, performance, or testing requirements added only to a skill or agent-only file, or integration-specific guidance added to generic contributor docs instead of the integration.
- Not when: the content only tells an agent how to operate (for example, a skill's command sequence).

## 6. CI

### DES-6.1: Reuse wheels from the pipeline instead of rebuilding them per job
- Flag: a CI job that builds wheels or native extensions per Python version when the parent pipeline already builds them, waits on `upload all` instead of the specific build jobs, or copies native modules into the source checkout instead of installing the wheel.

### DES-6.2: Don't grow CI job count or reduce merge-queue coverage
- Flag: raising `MAX_TOTAL_TEST_JOBS`, splitting suites into more jobs instead of packing venvs into a target duration, or narrowing test selection in the merge queue, which must run the full suite.

## Don't flag
- Pre-existing contrib span code that the PR moves or touches only incidentally; suggest a follow-up instead.
