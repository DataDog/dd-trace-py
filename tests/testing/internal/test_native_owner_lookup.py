"""Owner lookup of the native line collector: reading the context variable's cache must credit each line
to the context PyContextVar_Get() names.

Every test runs two copies of the same module, one with the cache read and one with PyContextVar_Get()
only, and compares what each context, and the unowned record, got from each copy.
"""

import os
import subprocess
import sys
import sysconfig
import textwrap

import pytest


if sys.version_info < (3, 12):
    pytest.skip("native line coverage needs sys.monitoring", allow_module_level=True)
pytest.importorskip("ddtrace.internal.coverage._linecov")

# The collector reads the cache on CPython 3.12 to 3.14 with the GIL.
CACHE_READ = sys.version_info < (3, 15) and not sysconfig.get_config_var("Py_GIL_DISABLED")

WORKLOAD = """
import asyncio
import threading


def on_main(n):
    total = 0
    for i in range(n):
        total += i
    return total


def in_copied_context(n):
    total = 1
    for i in range(n):
        total *= 2
    return total


def in_empty_context(n):
    values = []
    for i in range(n):
        values.append(i)
    return values


def in_own_window(n):
    text = ""
    for i in range(n):
        text += str(i)
    return text


async def task_of_the_window(n):
    count = 0
    for i in range(n):
        await asyncio.sleep(0)
        count += 1
    return count


async def task_without_context(n):
    seen = []
    for i in range(n):
        await asyncio.sleep(0)
        seen.append(i)
    return seen


async def task_of_another_context(n):
    pairs = {}
    for i in range(n):
        await asyncio.sleep(0)
        pairs[i] = i
    return pairs


def suspended(n):
    total = 0
    for i in range(n):
        total += i
        yield total
    yield -total
"""


HARNESS = """
import asyncio
import contextvars
import sys
import threading

from ddtrace.internal.coverage import _linecov
from ddtrace.internal.coverage import native_lines

sys.monitoring.use_tool_id(5, "test")
assert native_lines.init(5)
assert _linecov.stats()["cached_owner"] == CACHE_READ, _linecov.stats()
ROOT = "/ddtrace-owner-test"
native_lines.set_report_root(ROOT)
# Many GIL switches: the threads below take turns with the main thread while they run.
sys.setswitchinterval(1e-6)


def load(name):
    path = ROOT + "/" + name + ".py"
    code = compile(WORKLOAD, path, "exec")
    native_lines.instrument(code, path)
    namespace = {"__name__": name}
    exec(code, namespace)
    return path, namespace


CACHED, CACHED_NS = load("cached")
CALLED, CALLED_NS = load("called")


def line_set(bitmap):
    if bitmap is None:
        return set()
    return {i * 8 + b for i, byte in enumerate(bitmap) for b in range(8) if byte & (0x80 >> b)}


def covered(context, path):
    return line_set(context.covered().get(path))


def unowned(path):
    return line_set(dict(_linecov.shared()["unowned"]).get(path[len(ROOT):]))


def lines_of(namespace, name):
    code = namespace[name].__code__
    return {line for _, _, line in code.co_lines() if line is not None and line != code.co_firstlineno}


def same(label, cached, called):
    assert cached == called, "%s: cache %s, call %s, missing %s, extra %s" % (
        label, sorted(cached), sorted(called), sorted(called - cached), sorted(cached - called)
    )
"""


def _run_isolated(script: str) -> None:
    # A subprocess: the collector registers a sys.monitoring tool and process-wide state.
    source = "WORKLOAD = %r\nCACHE_READ = %r\n" % (WORKLOAD, int(CACHE_READ)) + HARNESS + textwrap.dedent(script)
    result = subprocess.run(
        [sys.executable, "-c", source],
        env={**os.environ, "_DD_COVERAGE_NATIVE_LINES": "true"},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_threads_tasks_and_windows_get_the_same_lines() -> None:
    """Threads in a copy of the window's context, in an empty context and in a window of their own run
    alongside the main thread; tasks of three contexts take turns on one event loop; a generator is
    resumed in a window that is the only active context. Each context, and the unowned record, gets
    the same lines whether the owner comes from the cache or from PyContextVar_Get().
    """
    _run_isolated(
        """
        results = {}
        for path, namespace in ((CACHED, CACHED_NS), (CALLED, CALLED_NS)):
            assert _linecov.set_cached_owner(path == CACHED) == (CACHE_READ and path == CACHED)
            window, own, other, resumed = _linecov.Context(), _linecov.Context(), _linecov.Context(), _linecov.Context()
            other.push()
            other_context = contextvars.Context()
            other_context.run(native_lines.ctx_native.set, other)

            def own_window(n):
                previous = own.enter()
                try:
                    namespace["in_own_window"](n)
                finally:
                    own.exit(previous)

            async def tasks(n):
                loop = asyncio.get_running_loop()
                await asyncio.gather(
                    loop.create_task(namespace["task_of_the_window"](n)),
                    loop.create_task(namespace["task_without_context"](n), context=contextvars.Context()),
                    loop.create_task(namespace["task_of_another_context"](n), context=other_context),
                )

            previous = window.enter()
            try:
                copied, empty = contextvars.copy_context(), contextvars.Context()
                threads = [
                    threading.Thread(target=copied.run, args=(namespace["in_copied_context"], 2000)),
                    threading.Thread(target=empty.run, args=(namespace["in_empty_context"], 2000)),
                    threading.Thread(target=own_window, args=(2000,)),
                ]
                for thread in threads:
                    thread.start()
                namespace["on_main"](2000)
                for thread in threads:
                    thread.join(30)
                asyncio.run(tasks(50))
                generator = namespace["suspended"](3)
                next(generator)
            finally:
                window.exit(previous)
            other.pop()
            # The only active context from here: the callbacks take its fast path.
            previous = resumed.enter()
            try:
                assert _linecov.stats()["active"] == 1, _linecov.stats()
                list(generator)
            finally:
                resumed.exit(previous)
            results[path] = {
                "window": covered(window, path),
                "own": covered(own, path),
                "other": covered(other, path),
                "resumed": covered(resumed, path),
                "unowned": unowned(path),
            }
        for name in results[CACHED]:
            same(name, results[CACHED][name], results[CALLED][name])

        cached = results[CACHED]
        for name in ("on_main", "in_copied_context", "task_of_the_window"):
            assert lines_of(CACHED_NS, name) <= cached["window"], (name, cached)
        assert lines_of(CACHED_NS, "in_own_window") <= cached["own"], cached
        assert lines_of(CACHED_NS, "task_of_another_context") <= cached["other"], cached
        for name in ("in_empty_context", "task_without_context"):
            assert lines_of(CACHED_NS, name) <= cached["unowned"], (name, cached)
        assert lines_of(CACHED_NS, "in_empty_context").isdisjoint(cached["window"]), cached
        assert cached["resumed"] and cached["resumed"] <= lines_of(CACHED_NS, "suspended"), cached
        assert _linecov.stats()["errors"] == 0, _linecov.stats()
        """
    )


def test_cache_self_test_leaves_the_running_context_as_it_was() -> None:
    """The self-test that switching the cache read on runs again uses temporary contexts only: the
    thread's own context object, its variables and their values are unchanged, outside a window and
    inside one.
    """
    _run_isolated(
        """
        application = contextvars.ContextVar("application")
        value = object()
        application.set(value)
        window = _linecov.Context()
        for inside in (False, True):
            previous = window.enter() if inside else None
            try:
                before = list(contextvars.copy_context().items())
                assert _linecov.set_cached_owner(True) == CACHE_READ
                after = list(contextvars.copy_context().items())
                assert len(before) == len(after), (before, after)
                assert all(a[0] is b[0] and a[1] is b[1] for a, b in zip(before, after)), (before, after)
                assert application.get() is value
                assert (native_lines.ctx_native.get() is window) == inside
            finally:
                if inside:
                    window.exit(previous)
        assert _linecov.set_cached_owner(False) is False
        assert _linecov.stats()["cached_owner"] == 0
        """
    )
