# Wrapping compatibility suite

Hand-written tests that exercise dd-trace-py's function-wrapping / monkey-patching
machinery against the full range of real-world Python callable shapes.

Real applications combine callables in ways our older tests didn't — classmethods,
async generators under `@asynccontextmanager`, positional-only signatures, decorator
stacks in surprising orders, version-specific syntax. Most of the "small
incompatibility" wrapping bugs we hit (e.g. PR #18718, PR #18741) were shapes no
test exercised. This suite covers them explicitly.

## How it's organised

Every test defines a real, readable callable and asserts its **explicit expected
behaviour** after wrapping — no generated/`exec`'d source, so each case is
auditable, lintable, and type-checkable. Tests are grouped by callable category:

| File | Covers |
|------|--------|
| `test_functions.py` | plain functions: signatures (posonly `/`, kwonly `*`, `*args`, `**kwargs`, defaults, full, annotated), closures & cell vars, lambdas, recursion, exceptions & traceback fidelity, decorator order, `lru_cache` |
| `test_methods.py` | instance / class / static methods, `property` getters, callable instances, and method generators/coroutines/async-gens |
| `test_generators.py` | iterate, send, throw (recover/propagate), close+finally, **return value**, `yield from` (+ throw delegation), throw-into-unstarted |
| `test_async.py` | coroutines (return/raise/cancel+finally, multi-suspend await) and async generators (iterate, asend, athrow, aclose+finally, multi-suspend-before-yield) |
| `test_context_managers.py` | sync `@contextmanager` and `@asynccontextmanager` over wrapped (async) generators — the PR #18718 / #18741 real-world shape |
| `test_introspection.py` | `inspect.signature`, `__name__`/`__doc__`, annotations, and `is{generator,coroutine,asyncgen}function` after wrapping |

Each test is parametrized over the **four wrapping mechanisms** (`_harness.mechanisms`):

- `internal_wrap` — `ddtrace.internal.wrapping.wrap` (bytecode trampoline)
- `tracer_wrap` — the public `@tracer.wrap()` decorator
- `wrapt` — `wrapt.wrap_function_wrapper`
- `wrapping_context` — a no-op `WrappingContext` subclass (the DI/IAST/AppSec/profiling path)

## Version-specific files

Files named `test_*_py<NN>.py` use syntax newer than the project minimum and are
collected **only on that interpreter or newer**, via `conftest.py`'s `collect_ignore`
(mirroring `tests/appsec/iast/aspects/conftest.py`). They are also excluded from the
min-version `ruff` parser in `pyproject.toml`, and linted by the matching CI Python.

| File | Min | Construct |
|------|-----|-----------|
| `test_match_py310.py` | 3.10 | `match`/`case` |
| `test_except_star_py311.py` | 3.11 | `except*` exception groups |
| `test_pep695_py312.py` | 3.12 | PEP 695 generics + `__type_params__` |
| `test_tstrings_py314.py` | 3.14 | PEP 750 template strings (`t"..."`) |
