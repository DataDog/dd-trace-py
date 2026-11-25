"""Block probes of the native line collector (CPython 3.12): they must credit the lines LINE events do.

Every test runs two copies of the same module, one decided on LINE events and one on block probes,
and compares what each context, and the unowned record, got from each copy.
"""

import os
import subprocess
import sys
import textwrap

import pytest


if sys.version_info[:2] != (3, 12):
    pytest.skip("block probes are built for CPython 3.12 only", allow_module_level=True)
_linecov = pytest.importorskip("ddtrace.internal.coverage._linecov")
if not getattr(_linecov, "HAVE_BLOCK_PROBES", False):
    pytest.skip("this build has no block probes", allow_module_level=True)


SCENARIOS = """
import contextlib


def branches(n):
    total = 0
    for i in range(n):
        if i % 3 == 0:
            continue
        elif i % 3 == 1:
            total += i
        else:
            total -= 1
            if total < -5:
                break
    else:
        total += 100
    while n > 0:
        n -= 2
        if n == 3:
            return total
    return total


def handled(n):
    result = []
    try:
        result.append(1)
        if n % 2:
            raise ValueError(n)
        result.append(2)
    except ValueError:
        result.append(3)
        if n > 2:
            try:
                raise KeyError(n)
            except KeyError:
                result.append(4)
    else:
        result.append(5)
    finally:
        result.append(6)
    return result


def callee_raises(n):
    x = n + 1
    if x > 2:
        raise RuntimeError(x)
    y = x * 2
    return y


def caller(n):
    try:
        value = callee_raises(n)
        value += 1
    except RuntimeError:
        value = -1
    return value


def leaks(n):
    a = callee_raises(n)
    b = a + 1
    return b


def leak_caller(n):
    try:
        return leaks(n)
    except RuntimeError:
        return -1


def reraise(n):
    try:
        try:
            raise ValueError(n)
        except ValueError:
            if n > 1:
                raise
            return 0
    except ValueError:
        return 1


def finally_flow(n):
    for i in range(3):
        try:
            if i == n:
                break
            if i > n:
                continue
        finally:
            if n == 2:
                return i
    return -1


class Resource:
    def __init__(self):
        self.items = []

    def __enter__(self):
        return self.items

    def __exit__(self, *exc):
        self.items.append("exit")
        return False


def managed(n):
    with contextlib.suppress(ZeroDivisionError):
        x = 1 / (n % 2)
        x += 1
    with Resource() as items:
        items.append(n)
    return n


def gen(n):
    for i in range(n):
        try:
            yield i
        except KeyError:
            yield -1
    yield from range(2)
    return n


def generators(n):
    g = gen(n)
    values = [next(g, None)]
    if n > 1:
        values.append(g.throw(KeyError()))
    values.extend(g)
    closed = gen(n + 2)
    next(closed)
    closed.close()
    return values


async def agen(n):
    for i in range(n):
        yield i


class Awaitable:
    def __await__(self):
        yield None
        return 5


class AsyncResource:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


async def failing(n):
    if n > 1:
        raise ValueError(n)


async def coro(n):
    total = 0
    async for i in agen(n):
        total += i
    async with AsyncResource():
        total += await Awaitable()
    try:
        await failing(n)
    except ValueError:
        total -= 1
    return total


def coroutines(n):
    c = coro(n)
    try:
        while True:
            c.send(None)
    except StopIteration as stop:
        return stop.value


def deep(n):
    if n <= 0:
        raise LookupError("bottom")
    return deep(n - 1)


def deep_caller(n):
    try:
        deep(n)
    except LookupError:
        return n


def groups(n):
    try:
        raise ExceptionGroup("g", [ValueError(n), KeyError(n)])
    except* ValueError:
        n += 1
    except* KeyError:
        n += 2
    return n


ALL = [
    branches,
    handled,
    caller,
    leak_caller,
    reraise,
    finally_flow,
    managed,
    generators,
    coroutines,
    deep_caller,
    groups,
]


def switching(n, new_context):
    a = n + 1
    inner = new_context()
    previous = inner.enter()
    b = branches(n)
    c = a + b
    inner.exit(previous)
    d = c * 2
    if d > 3:
        d -= 1
    return d, inner


def worker(started, release, results):
    x = 1
    started.set()
    release.wait(30)
    y = x + 1
    if y:
        y += 1
    results.append(y)


def suspended(n):
    total = 0
    for i in range(n):
        total += i
        yield total
    total -= 1
    yield total
"""


HARNESS = """
import sys
import threading

from ddtrace.internal.coverage import _linecov
from ddtrace.internal.coverage import native_lines

sys.monitoring.use_tool_id(5, "test")
assert native_lines.init(5)
assert _linecov.stats()["block_probes"] == 1, _linecov.stats()
INSTRUCTION_PROBES = bool(_linecov.stats()["instruction_probes"])
ROOT = "/ddtrace-block-test"
native_lines.set_report_root(ROOT)


def load(name):
    path = ROOT + "/" + name + ".py"
    code = compile(SCENARIOS, path, "exec")
    native_lines.instrument(code, path)
    namespace = {"__name__": name}
    exec(code, namespace)
    return path, namespace


LINES, LINES_NS = load("lines")
BLOCKS, BLOCKS_NS = load("blocks")


class line_probes:
    # Code objects decide their probes at their first event: the copy run in here decides on LINE.
    def __enter__(self):
        _linecov.set_block_probes(False)
        _linecov.set_instruction_probes(False)

    def __exit__(self, *exc):
        _linecov.set_instruction_probes(INSTRUCTION_PROBES)
        _linecov.set_block_probes(True)


def line_set(bitmap):
    if bitmap is None:
        return set()
    return {i * 8 + b for i, byte in enumerate(bitmap) for b in range(8) if byte & (0x80 >> b)}


def covered(context, path):
    return line_set(context.covered().get(path))


def unowned(report_path):
    return line_set(dict(_linecov.shared()["unowned"]).get(report_path))


def same(label, lines, blocks):
    assert lines == blocks, "%s: LINE %s, blocks %s, missing %s, extra %s" % (
        label, sorted(lines), sorted(blocks), sorted(lines - blocks), sorted(blocks - lines)
    )
"""


def _run_isolated(script: str) -> None:
    # A subprocess: the collector registers a sys.monitoring tool and process-wide state.
    source = "SCENARIOS = " + repr(SCENARIOS) + "\n" + HARNESS + textwrap.dedent(script)
    result = subprocess.run(
        [sys.executable, "-c", source],
        env={**os.environ, "_DD_COVERAGE_NATIVE_LINES": "true", "_DD_COVERAGE_BLOCK_PROBES": "true"},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_scenarios_in_successive_contexts() -> None:
    """Branches, loops, handled and propagated exceptions, finally, with, generators, coroutines and
    exception groups, each run in a context of its own, with arguments that change the paths taken.
    """
    _run_isolated(
        """
        for n in range(5):
            for k, name in enumerate(f.__name__ for f in LINES_NS["ALL"]):
                context = _linecov.Context()
                previous = context.enter()
                try:
                    with line_probes():
                        expected = LINES_NS[name](n)
                    assert BLOCKS_NS[name](n) == expected
                finally:
                    context.exit(previous)
                same("%s(%d)" % (name, n), covered(context, LINES), covered(context, BLOCKS))

        probes = {name: _linecov.probes(BLOCKS_NS[name].__code__) for name in ("branches", "handled", "gen", "coro")}
        assert set(probes.values()) == {3}, probes
        assert all(_linecov.probes(f.__code__) == 1 for f in LINES_NS["ALL"])
        assert _linecov.stats()["errors"] == 0, _linecov.stats()
        """
    )


def test_blocks_describe_the_analysis() -> None:
    """blocks() lists each block's line starts, its immediate dominator and probe."""
    _run_isolated(
        """
        BLOCKS_NS["branches"](4)
        info = _linecov.blocks(BLOCKS_NS["branches"].__code__)
        blocks = info["blocks"]
        n_blocks = len(blocks)
        assert n_blocks > 3, info
        assert blocks[0][2] in (-1, 0), info
        assert all(-1 <= idom < n_blocks for _, _, idom, _ in blocks), info
        assert all(-1 <= block < n_blocks for _, block, _ in info["probes"]), info
        assert any(probed for _, _, _, probed in blocks), info
        lines = {line for _, block_lines, _, _ in blocks for line in block_lines}
        code = BLOCKS_NS["branches"].__code__
        body = {line for _, _, line in code.co_lines() if line is not None and line != code.co_firstlineno}
        assert lines == body, (sorted(lines), sorted(body))
        """
    )


def test_owner_switch_in_a_running_function() -> None:
    """A context entered and exited halfway through a running function: the lines before the switch
    are the outer context's, the ones in between the inner one's.
    """
    _run_isolated(
        """
        for n in (1, 4):
            results = {}
            for path, namespace in ((LINES, LINES_NS), (BLOCKS, BLOCKS_NS)):
                outer = _linecov.Context()
                previous = outer.enter()
                try:
                    if path == LINES:
                        with line_probes():
                            _, inner = namespace["switching"](n, _linecov.Context)
                    else:
                        _, inner = namespace["switching"](n, _linecov.Context)
                finally:
                    outer.exit(previous)
                results[path] = (covered(outer, path), covered(inner, path))
            same("outer %d" % n, results[LINES][0], results[BLOCKS][0])
            same("inner %d" % n, results[LINES][1], results[BLOCKS][1])
        """
    )


def test_nested_window_suspends_the_outer_one() -> None:
    """A window opened over another on the same thread: the outer context keeps no line armed while
    the inner one runs, and gets the lines it runs after the inner window closes.
    """
    _run_isolated(
        """
        results = {}
        for path, namespace in ((LINES, LINES_NS), (BLOCKS, BLOCKS_NS)):
            outer, inner = _linecov.Context(), _linecov.Context()
            probes = line_probes() if path == LINES else None
            if probes:
                probes.__enter__()
            try:
                outer_previous = outer.enter()
                inner_previous = inner.enter()
                kept = _linecov.stats()["events_kept"]
                for n in range(6):
                    namespace["branches"](n)
                assert _linecov.stats()["events_kept"] == kept, _linecov.stats()
                inner.exit(inner_previous)
                for n in range(6):
                    namespace["branches"](n)
                outer.exit(outer_previous)
            finally:
                if probes:
                    probes.__exit__(None, None, None)
            results[path] = (covered(outer, path), covered(inner, path))
        same("outer", results[LINES][0], results[BLOCKS][0])
        same("inner", results[LINES][1], results[BLOCKS][1])
        assert results[BLOCKS][0] == results[BLOCKS][1] != set(), results
        """
    )


def test_window_on_another_thread_is_not_suspended() -> None:
    """A context whose window is open on another thread keeps lines armed while windows nest here,
    so it gets the lines it runs after this thread ran them.
    """
    _run_isolated(
        """
        results = {}
        for path, namespace in ((LINES, LINES_NS), (BLOCKS, BLOCKS_NS)):
            other, outer, inner = _linecov.Context(), _linecov.Context(), _linecov.Context()
            ready, go = threading.Event(), threading.Event()

            def target():
                previous = other.enter()
                ready.set()
                go.wait(30)
                namespace["branches"](4)
                other.exit(previous)

            probes = line_probes() if path == LINES else None
            if probes:
                probes.__enter__()
            try:
                thread = threading.Thread(target=target)
                thread.start()
                assert ready.wait(30)
                outer_previous = outer.enter()
                inner_previous = inner.enter()
                namespace["branches"](4)
                inner.exit(inner_previous)
                outer.exit(outer_previous)
                go.set()
                thread.join(30)
            finally:
                if probes:
                    probes.__exit__(None, None, None)
            results[path] = (covered(other, path), covered(inner, path))
        same("other", results[LINES][0], results[BLOCKS][0])
        assert results[BLOCKS][0] == results[BLOCKS][1] != set(), results
        """
    )


def test_window_closing_on_another_thread() -> None:
    """A thread carrying a context while the context's window closes: what it ran before the close
    is the context's, what it runs after is unowned.
    """
    _run_isolated(
        """
        results = {}
        for path, namespace in ((LINES, LINES_NS), (BLOCKS, BLOCKS_NS)):
            context = _linecov.Context()
            started, release, values = threading.Event(), threading.Event(), []

            def target():
                native_lines.ctx_native.set(context)
                namespace["worker"](started, release, values)

            probes = line_probes() if path == LINES else None
            if probes:
                probes.__enter__()
            try:
                previous = context.enter()
                thread = threading.Thread(target=target)
                thread.start()
                assert started.wait(30)
                context.exit(previous)
                release.set()
                thread.join(30)
            finally:
                if probes:
                    probes.__exit__(None, None, None)
            assert values == [3]
            report_path = path[len(ROOT):]
            results[path] = (covered(context, path), unowned(report_path))
        same("context", results[LINES][0], results[BLOCKS][0])
        same("unowned", results[LINES][1], results[BLOCKS][1])
        assert results[BLOCKS][1], results
        """
    )


def test_generator_resumed_in_other_contexts() -> None:
    """A suspended generator resumed in another context: each context gets what ran while it was the
    owner.
    """
    _run_isolated(
        """
        results = {}
        for path, namespace in ((LINES, LINES_NS), (BLOCKS, BLOCKS_NS)):
            contexts = [_linecov.Context() for _ in range(3)]
            probes = line_probes() if path == LINES else None
            if probes:
                probes.__enter__()
            try:
                g = namespace["suspended"](2)
                for context in contexts:
                    previous = context.enter()
                    try:
                        next(g)
                    finally:
                        context.exit(previous)
            finally:
                if probes:
                    probes.__exit__(None, None, None)
            results[path] = [covered(context, path) for context in contexts]
        for k in range(3):
            same("context %d" % k, results[LINES][k], results[BLOCKS][k])
        """
    )


def test_disabling_blocks_mid_run() -> None:
    """disable_blocks(), as on a greenlet import, credits what block frames ran so far and moves
    their code to line-start probes, which finish the frames the way LINE events would.
    """
    _run_isolated(
        """
        def run(path, namespace):
            first, second = _linecov.Context(), _linecov.Context()
            previous = first.enter()
            g = namespace["suspended"](3)
            next(g)
            next(g)
            if path == BLOCKS:
                assert _linecov.probes(namespace["suspended"].__code__) == 3
                _linecov.disable_blocks()
                assert _linecov.probes(namespace["suspended"].__code__) != 3
            first.exit(previous)
            previous = second.enter()
            list(g)
            namespace["branches"](4)
            second.exit(previous)
            return covered(first, path), covered(second, path)

        results = {}
        with line_probes():
            results[LINES] = run(LINES, LINES_NS)
        results[BLOCKS] = run(BLOCKS, BLOCKS_NS)
        same("first", results[LINES][0], results[BLOCKS][0])
        same("second", results[LINES][1], results[BLOCKS][1])
        assert _linecov.stats()["block_probes"] == 0
        """
    )
