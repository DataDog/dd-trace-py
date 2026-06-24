# Implementation Guide

Step-by-step for creating a new dd-trace-py integration.
Each step points to a real file -- read it, don't guess the pattern.

## 1. Create the patch module

Create `ddtrace/contrib/internal/{name}/__init__.py` with RST docstrings
and exports. See `ddtrace/contrib/internal/anthropic/__init__.py` for format.

Create `ddtrace/contrib/internal/{name}/patch.py` with `get_version()`, `_supported_versions`,
`patch()`, and `unpatch()`. Choose the right pattern:
- **Events API (NEW — preferred for new standard integrations)**: Use `context_with_event()` + `TracingEvent` subclasses + `TracingSubscriber`. Read concrete examples first:
  - `ddtrace/contrib/internal/httpx/patch.py` — sync and async HTTP with `context_with_event()`
  - `ddtrace/contrib/internal/aiohttp/patch.py` — async HTTP client with `context_with_event()`
  - `ddtrace/_trace/events.py` — `TracingEvent` base class (subclass this per-event)
  - `ddtrace/_trace/subscribers/_base.py` — `TracingSubscriber` base class (handles span creation)
  - `ddtrace/internal/core/__init__.py` — `context_with_event()` API
  - `ddtrace/internal/core/events.py` — `Event` base + `event_field()` descriptor
- **`context_with_data()` (existing pattern)**: Use `context_with_data()` + `trace_handlers.py`. Read `ddtrace/contrib/internal/flask/patch.py` or another existing `context_with_data` integration. Use this when extending or mirroring an existing `context_with_data` integration.
- **Pin + `tracer.trace()` (DEPRECATED)**: Do NOT use in new integrations.
- **LLM/AI integrations**: Use this guide for contrib package layout, registration, config, and APM tests. Use the `llmobs-integrations` skill for LLM-specific patch patterns, span lifecycle, `BaseLLMIntegration`, stream handling, extraction, and LLMObs tests.

### Type Annotations

All functions in the patch module must have full type annotations. Follow the typing patterns from the reference integration you read in the step above.

```python
from typing import Any, Callable, Optional

def get_version() -> str:
    ...

def _supported_versions() -> str:
    ...

def patch() -> None:
    ...

def unpatch() -> None:
    ...

# Wrapper functions use the wrapt signature:
def traced_request(
    wrapped: Callable[..., Any],
    instance: Any,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> Any:
    ...
```

## 2. Register the integration

Add to `ddtrace/_monkey.py` `PATCH_MODULES` dict in alphabetical order.
If the module name we patched is different than the contrib name, e.g. we patched `snowflake.core`, but the main package is `snowflake`, add it to `MODULES_FOR_CONTRIB` as well.
Add to `scripts/integration_registry/registry.yaml` with dependency
names and tested version range.
Add to `INTEGRATION_CONFIGS` frozenset in `ddtrace/internal/settings/_config.py`
in alphabetical order — this allows users to configure the integration before
it's patched. **Without this entry, integration config settings are silently ignored.**

## 3. Create LLMObs integration (LLM only)

Create `ddtrace/llmobs/_integrations/{name}.py` subclassing `BaseLLMIntegration`.
Read `ddtrace/llmobs/_integrations/anthropic.py` for the canonical pattern.
Register in `ddtrace/llmobs/_integrations/__init__.py` (import + `__all__`).

See the **llmobs-integrations** skill for the full LLM-specific implementation guide.

## 4. Add test environment

### riotfile.py

Add a `Venv()` entry to `riotfile.py` near similar integrations. Place it alphabetically within the appropriate section.

```python
Venv(
    name="{name}",
    command="pytest {cmdargs} tests/contrib/{name}",
    pkgs={
        "pytest-asyncio": latest,  # if async support
        "vcrpy": latest,           # if the suite uses vcrpy cassettes; match nearby pins
    },
    venvs=[
        # Pin oldest supported version
        Venv(
            pys=select_pys(min_version="3.9", max_version="3.13"),
            pkgs={"{package}": "~=1.0.0"},
        ),
        # Latest version
        Venv(
            pys=select_pys(),
            pkgs={"{package}": latest},
        ),
    ],
),
```

Key rules:
- `name` must match the integration name used in `PATCH_MODULES`
- `command` points to the test directory
- Add `vcrpy` only when the suite uses vcrpy cassettes; follow nearby integrations for `latest` vs pinned versions
- Use `select_pys()` for Python version ranges
- Pin oldest supported + latest in separate nested `Venv()` entries
- Check neighboring entries in the file for consistent formatting

### suitespec.yml

Add **component** and **suite** entries to the correct suitespec file:
- LLM/AI integrations: `tests/llmobs/suitespec.yml`
- Standard integrations: `tests/contrib/suitespec.yml`

Look at a similar integration's entry in the same suitespec file and follow the same pattern for both the `components:` and `suites:` sections.

## 5. Write tests

Create `tests/contrib/{name}/` with:

| File | Purpose | Pattern to follow |
|------|---------|-------------------|
| `test_{name}_patch.py` | Patch/unpatch cycle | `tests/contrib/anthropic/test_anthropic_patch.py` |
| `test_{name}.py` | APM span assertions | Prefer `@pytest.mark.snapshot` |
| `test_{name}_llmobs.py` | LLMObs (LLM only) | `tests/contrib/anthropic/test_anthropic_llmobs.py` |

LLMObs tests should use `assert_llmobs_span_data()` from `tests/llmobs/_utils.py` with `_get_llmobs_data_metastruct(span)` from `ddtrace.llmobs._utils`.
See the **llmobs-integrations** skill's Testing Guide for VCR cassette setup and assertion patterns.

## 6. Add documentation (non-LLM only)

Add automodule entry to `docs/integrations.rst` in alphabetical order.

## 7. Generate release note

Use the **releasenote** skill.

## Verification

Use the **run-tests** skill to run tests. Use the **lint** skill for formatting and type checks. Never invoke `pytest`, `riot`, or `scripts/ddtest` directly.

## Recent PRs as Examples

- vLLM (#14732) -- LLM integration
- Azure EventHubs (#14636) -- message queue
- httpx revamp (#15522) -- HTTP client

## Complete Checklist

Every new integration must complete ALL applicable items:

### Registration
- [ ] `ddtrace/_monkey.py` -- `PATCH_MODULES` entry in alphabetical order
- [ ] `scripts/integration_registry/registry.yaml` -- dependency names and tested version range
- [ ] `ddtrace/internal/settings/_config.py` -- `INTEGRATION_CONFIGS` frozenset entry
- [ ] `ddtrace/llmobs/_integrations/__init__.py` -- import + `__all__` entry (LLM only)

### Patch Code
- [ ] `ddtrace/contrib/internal/{name}/patch.py` -- `patch()`, `unpatch()`, `get_version()`
- [ ] Standard patch code uses `<Type>Event` + `core.context_with_event()`; specialized direct child spans follow the closest current reference
- [ ] Prefer class-based events API (`TracingEvent` + `TracingSubscriber`) for new standard integrations
- [ ] LLM integrations follow the `llmobs-integrations` skill for request span lifecycle and LLMObs extraction

### Integration Layer
- [ ] `ddtrace/llmobs/_integrations/{name}.py` -- `BaseLLMIntegration` subclass (LLM only)
- [ ] `_set_base_span_tags()` implemented
- [ ] `_llmobs_set_tags()` implemented with all required LLMObs fields (LLM only)

### Test Environment
- [ ] `riotfile.py` -- `Venv()` entry with pinned + latest package versions
- [ ] Suitespec entry -- component + suite in correct file (`tests/llmobs/suitespec.yml` or `tests/contrib/suitespec.yml`)
- [ ] Compile and prune test requirements if needed -- follow `docs/contributing-testing.rst` and existing repo workflow

### Tests
- [ ] `tests/contrib/{name}/test_{name}_patch.py` -- patch/unpatch cycle tests
- [ ] `tests/contrib/{name}/test_{name}.py` -- APM span snapshot tests
- [ ] `tests/contrib/{name}/test_{name}_llmobs.py` -- LLMObs assertion tests (LLM only)
- [ ] Tests use `@pytest.mark.snapshot` for APM assertions where possible
- [ ] LLMObs tests use `assert_llmobs_span_data(_get_llmobs_data_metastruct(span), ...)`

### Type Annotations
- [ ] All function signatures have type annotations (parameters + return type)
- [ ] No bare `dict`, `list`, `tuple` — use `dict[str, Any]`, `list[str]`, etc.
- [ ] `Optional[X]` used for nullable parameters and return values
- [ ] Wrapper functions use typed wrapt signature (`Callable[..., Any]`, `tuple[Any, ...]`, etc.)
- [ ] No unjustified `# type: ignore` comments
- [ ] **lint** skill typing check (`scripts/lint typing`) passes with no errors

### Documentation
- [ ] `docs/integrations.rst` -- automodule entry in alphabetical order (non-LLM only)
- [ ] `docs/index.rst` -- add integration to the docs index
- [ ] Release note -- use the **releasenote** skill
