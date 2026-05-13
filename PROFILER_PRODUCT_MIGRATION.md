# Profiler → Product Protocol Migration

## Context

The Profiler is one of the last components still bootstrapped via ad-hoc logic in
`ddtrace/bootstrap/preload.py` instead of the `Product` protocol
(`ddtrace/internal/products.py`).

**Previous attempt**: [PR #14571](https://github.com/DataDog/dd-trace-py/pull/14571)
by @taegyunkim — closed automatically due to inactivity. The approach was sound;
codebase has evolved since (v4.0, fork-handling rework).

**Related open PRs** (may land before/after):
- [#16601](https://github.com/DataDog/dd-trace-py/pull/16601) — adds `enabled()` to Product protocol
- [#16711](https://github.com/DataDog/dd-trace-py/pull/16711) — replaces `at_exit` with `skip_exit`

---

## Steps

### 1. Create `ddtrace/profiling/product.py`

Module-level product implementing the `Product` protocol (like `errortracking/product.py`).

```python
from ddtrace.internal.settings.profiling import config

requires: list[str] = []

def post_preload() -> None: ...
def start() -> None: ...
def restart(join: bool = False) -> None: ...
def stop(join: bool = False) -> None: ...
def at_exit(join: bool = False) -> None: ...
```

`start()` should:
- Check `config.enabled`, bail if not
- Platform checks (32-bit Linux, Windows) — log error and bail
- Create `Profiler` instance and call `.start()`
- Store instance for `restart`/`stop`

**Key insight**: `ProductManager.run_protocol()` already handles the uWSGI
master/worker lifecycle. The product's `start()` just starts the profiler
directly — no uWSGI dance needed at this level.

`restart()`: Likely a no-op. The profiler uses native `pthread_atfork` handlers
+ `PeriodicThread` auto-restart for regular forks. The `ProductManager` handles
the uWSGI fork flow. **Needs verification** — does `restart()` need to do
anything or do the native handlers cover it?

`stop()`: Stop the profiler instance if running.

`at_exit()`: Delegates to `stop()`.

### 2. Simplify `Profiler.start()` / `Profiler.stop()`

Remove responsibilities that the `ProductManager` now owns:

- [ ] Remove `uwsgi.check_uwsgi()` call from `Profiler.start()`
- [ ] Remove `atexit.register(self.stop)` / `atexit.unregister(self.stop)`
- [ ] Remove `telemetry_writer.product_activated()` calls (manager does this)
- [ ] Keep `_start_on_fork` if `restart()` needs it

### 3. Lazy import in `ddtrace/profiling/__init__.py`

Prevent product discovery from triggering a full profiling import:

```python
from ddtrace.internal.module import lazy

@lazy
def _():
    from ddtrace.profiling.profiler import Profiler  # noqa:F401
```

Add `ddtrace/profiling/__init__.pyi` for type-checking.

### 4. Register in `pyproject.toml`

```toml
[project.entry-points.'ddtrace.products']
"profiling" = "ddtrace.profiling.product"
```

### 5. Remove profiling block from `preload.py`

Delete lines 57-62 of `ddtrace/bootstrap/preload.py`:

```python
if profiling_config.enabled:
    log.debug("profiler enabled via environment variable")
    try:
        import ddtrace.profiling.auto  # noqa: F401
    except Exception:
        log.error("failed to enable profiling", exc_info=True)
```

### 6. Update `ddtrace/profiling/bootstrap/sitecustomize.py`

Still needed for backward compat (`import ddtrace.profiling.auto`). Guard
against double-start if the product manager already started profiling.

### 7. Handle `bootstrap.profiler` references

Several files access `ddtrace.profiling.bootstrap.profiler`. Options:
- Keep setting `bootstrap.profiler` from within the product module
- Or redirect references to a new location

Files that reference it:
- `tests/profiling/simple_program_fork.py`
- `tests/profiling/simple_program.py`
- `tests/profiling/_test_multiprocessing.py`
- `tests/contrib/gunicorn/wsgi_mw_app.py`
- `tests/commands/ddtrace_run_profiling.py`
- `ddtrace/profiling/bootstrap/sitecustomize.py`

### 8. Update tests

| File | Change |
|------|--------|
| `tests/profiling/simple_program_fork.py` | Use `ddtrace.auto`; set `DD_PROFILING_ENABLED=1` |
| `tests/profiling/test_main.py` | Add `DD_PROFILING_ENABLED=1` where needed |
| `tests/profiling/test_uwsgi.py` | Add `DD_PROFILING_ENABLED=1`; adjust timeouts |
| `tests/profiling/uwsgi-app.py` | Use `ddtrace.auto` instead of `ddtrace.profiling.auto` |
| `tests/profiling/gevent_fork.py` | Adjust `Profiler().start()` call |
| `tests/profiling_v2/` | Mirror changes |
| `tests/contrib/gunicorn/wsgi_mw_app.py` | Access profiler via new path |
| `tests/telemetry/test_writer.py` | Minor adjustments if needed |

---

## Open Questions

1. **`restart()` semantics** — The native `pthread_atfork` handlers restart the
   sampling thread and `PeriodicThread` auto-restart handles the Scheduler. Does
   the product's `restart()` need to do anything, or is it a no-op?

2. **`bootstrap.profiler` storage** — Where should the product store the running
   `Profiler` instance? Keep on `bootstrap.profiler` for compat, or use
   `product._instance`?

3. **Manual `Profiler()` usage** — Users who create `Profiler()` directly and
   call `.start()` must still work. The simplified `Profiler.start()` (without
   uWSGI/atexit handling) is fine for product-managed usage, but does it break
   manual usage? Manual users would lose atexit cleanup. Acceptable?

4. **Coordination with #16601 / #16711** — If those merge first, add `enabled()`
   / replace `at_exit` with `skip_exit`. Trivial either way.

---

## Risks

- **uWSGI double-handling** — `ProductManager.run_protocol()` already detects
  uWSGI and defers `_do_products()` to post-fork. Old `Profiler.start()` also
  did this. Must not duplicate or skip.

- **Fork restart** — Native handlers + PeriodicThread already restart profiling
  on fork. The `ProductManager` also calls `restart()`. Must not conflict.

- **Backward compat** — `import ddtrace.profiling.auto` and manual `Profiler()`
  usage must keep working.

- **Telemetry double-reporting** — Manager calls `product_activated()` on
  `start_products()`. Old `Profiler.start()` also did this. Must remove from
  `Profiler` to avoid duplication.

---

## Analysis: singleton guard PR vs. Product migration

_(March 2026 — context for the "only allow one Profiler at a time" PR)_

### What the singleton guard PR does

Adds `_active_instance` + fork-safe `_active_lock` on `Profiler` to prevent
multiple concurrent profiler instances. When a second `Profiler.start()` is
called while one is already running, it logs an error and returns early. The
guard is also checked in `_start_on_fork`. This is a self-contained change that
lives entirely within the `Profiler` class.

### Why the singleton guard is not throwaway work

The Product interface does **not** provide singleton semantics. `ProductManager`
has no concept of "only one instance of this product's underlying resource." The
constraint that only one native profiler can be active lives at the `Profiler`
class level, regardless of whether the caller is the `ProductManager` or a user
script. The `_active_instance` / `_active_lock` code survives the Product
migration intact.

The singleton guard is also a **prerequisite** for safe Product migration: when
both the `ProductManager` and a manual user try to start profilers, the guard
prevents double-profiling and native-level corruption.

### Customer-facing behavior impact of Product migration

If the migration strips lifecycle logic from `Profiler.start()` (as Step 2
proposes), manual `Profiler()` users lose:

| Lost behavior | Impact |
|---------------|--------|
| `atexit.register(self.stop)` removed | Profiler not stopped on exit unless user calls `stop()` explicitly |
| `uwsgi.check_uwsgi()` removed | Manual `Profiler().start()` under uWSGI breaks (no master/worker dance) |
| `telemetry_writer.product_activated()` removed | Telemetry incorrectly reports profiler as inactive |

This is a **silent behavioral regression** — code that worked before silently
stops cleaning up or handling uWSGI correctly.

Two approaches exist:

| Approach | Trade-off |
|----------|-----------|
| Keep all lifecycle logic in `Profiler.start()`, product just delegates | Safe for manual users, but `Profiler` isn't really simplified — Product is just a wrapper |
| Strip lifecycle logic from `Profiler`, product owns it | Breaks manual usage; requires a deprecation cycle |

### Additional Product interface complications

- **`restart()` semantics are unclear.** Native `pthread_atfork` handlers +
  `PeriodicThread` auto-restart already handle forks. The Product's `restart()`
  could conflict with those if not carefully made a no-op.

- **Telemetry double-reporting.** Both `ProductManager.start_products()` and
  `Profiler.start()` call `product_activated`. The migration must remove one but
  not both, depending on whether manual usage is supported.

- **`bootstrap.profiler` storage.** Several test files and `sitecustomize.py`
  reference `ddtrace.profiling.bootstrap.profiler`. The product module would
  need to keep setting this for backward compat.

### Conclusion

The singleton guard PR is a safe, incremental step that is needed in both
worlds. The Product migration is a larger change that requires either (a)
keeping dual lifecycle paths (negating much of the simplification goal) or (b)
deprecating manual `Profiler()` lifecycle management, which needs planning and
a deprecation cycle.
