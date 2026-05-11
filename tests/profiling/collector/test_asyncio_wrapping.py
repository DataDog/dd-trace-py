"""Behavioural tests for the asyncio function-wrapping in
``ddtrace/profiling/_asyncio.py``.

These tests exercise the contract that the wrapping must guarantee — alias
identity preservation, function metadata preservation, and that each profiler
callback fires when the corresponding asyncio API is exercised. They are
deliberately implementation-agnostic: they pass against the bytecode-based
``ddtrace.internal.wrapping.wrap`` (main) and the setattr-based local
``_wrap`` helper introduced in this branch.

Each test runs in its own subprocess (via ``@pytest.mark.subprocess``)
because the wrapping mutates global asyncio state and cannot be safely
reset between tests.
"""

from __future__ import annotations

import os
import sys
from typing import Any

import pytest


# Tests that exercise ``stack.weak_link_tasks`` must skip under uvloop —
# uvloop tasks don't support weak-link tracking the same way asyncio's
# native tasks do. Mirrors the gate on ``tests/profiling/collector/
# test_asyncio_weak_links.py``.
_SKIP_ON_UVLOOP = pytest.mark.skipif(
    os.environ.get("USE_UVLOOP", "0") == "1",
    reason="uvloop does not support weak link detection the same way as asyncio",
)


# ---------------------------------------------------------------------------
# Callback-firing invariants — one test per wrap site
# ---------------------------------------------------------------------------


@pytest.mark.subprocess(err=None)
def test_gather_triggers_link_tasks() -> None:
    """``asyncio.gather(...)`` must invoke ``stack.link_tasks`` for each child,
    via the wrapped ``_GatheringFuture.__init__``.
    """
    import asyncio

    from tests.profiling.collector._asyncio_wrap_helpers import captured_link_calls
    from tests.profiling.collector._asyncio_wrap_helpers import started_profiler

    with started_profiler(), captured_link_calls("link_tasks") as recorded:

        async def child() -> int:
            await asyncio.sleep(0)
            return 1

        async def main() -> tuple[int, int]:
            t1 = asyncio.ensure_future(child())
            t2 = asyncio.ensure_future(child())
            await asyncio.gather(t1, t2)
            return id(t1), id(t2)

        t1_id, t2_id = asyncio.run(main())

        assert t1_id in recorded, "gather did not link first child"
        assert t2_id in recorded, "gather did not link second child"


@pytest.mark.subprocess(err=None)
def test_shield_triggers_link_tasks() -> None:
    """``asyncio.shield(awaitable)`` must invoke ``stack.link_tasks`` for the
    shielded future.
    """
    import asyncio

    from tests.profiling.collector._asyncio_wrap_helpers import captured_link_calls
    from tests.profiling.collector._asyncio_wrap_helpers import started_profiler

    with started_profiler(), captured_link_calls("link_tasks") as recorded:

        async def child() -> int:
            await asyncio.sleep(0)
            return 7

        async def main() -> int:
            return await asyncio.shield(child())

        assert asyncio.run(main()) == 7
        assert len(recorded) >= 1, "shield did not fire link_tasks"


@pytest.mark.subprocess(err=None)
def test_as_completed_triggers_link_tasks_per_child() -> None:
    """``asyncio.as_completed([c1, c2, c3])`` must invoke ``stack.link_tasks``
    once per coroutine.
    """
    import asyncio

    from tests.profiling.collector._asyncio_wrap_helpers import captured_link_calls
    from tests.profiling.collector._asyncio_wrap_helpers import started_profiler

    with started_profiler(), captured_link_calls("link_tasks") as recorded:

        async def child(x: int) -> int:
            await asyncio.sleep(0)
            return x

        async def main() -> list[int]:
            results = []
            for fut in asyncio.as_completed([child(i) for i in range(3)]):
                results.append(await fut)
            return sorted(results)

        assert asyncio.run(main()) == [0, 1, 2]
        assert len(recorded) >= 3, f"as_completed fired link_tasks {len(recorded)} times, expected >= 3"


@pytest.mark.subprocess(err=None)
def test_wait_triggers_link_tasks_per_future() -> None:
    """``asyncio.wait([t1, t2])`` calls ``asyncio.tasks._wait`` internally; the
    wrapper there must invoke ``stack.link_tasks`` once per future.
    """
    import asyncio

    from tests.profiling.collector._asyncio_wrap_helpers import captured_link_calls
    from tests.profiling.collector._asyncio_wrap_helpers import started_profiler

    with started_profiler(), captured_link_calls("link_tasks") as recorded:

        async def child() -> None:
            await asyncio.sleep(0)

        async def main() -> tuple[int, int]:
            t1 = asyncio.ensure_future(child())
            t2 = asyncio.ensure_future(child())
            await asyncio.wait([t1, t2])
            return id(t1), id(t2)

        t1_id, t2_id = asyncio.run(main())
        assert t1_id in recorded, "wait did not link first future"
        assert t2_id in recorded, "wait did not link second future"


@pytest.mark.skipif(sys.version_info < (3, 11), reason="TaskGroup is Python 3.11+")
@pytest.mark.subprocess(err=None)
def test_taskgroup_triggers_link_tasks() -> None:
    """``asyncio.TaskGroup().create_task(coro)`` must invoke
    ``stack.link_tasks`` for each task. TaskGroup is Python 3.11+ only.
    """
    import asyncio

    from tests.profiling.collector._asyncio_wrap_helpers import captured_link_calls
    from tests.profiling.collector._asyncio_wrap_helpers import started_profiler

    with started_profiler(), captured_link_calls("link_tasks") as recorded:

        async def child(x: int) -> int:
            await asyncio.sleep(0)
            return x

        async def main() -> list[int]:
            results: list[int] = []
            # mypy doesn't know about TaskGroup on older type stubs; the
            # skipif gate above means this only runs on 3.11+.
            async with asyncio.TaskGroup() as tg:  # type: ignore[attr-defined]
                tasks = [tg.create_task(child(i)) for i in range(3)]
            for t in tasks:
                results.append(t.result())
            return sorted(results)

        assert asyncio.run(main()) == [0, 1, 2]
        assert len(recorded) >= 3, f"TaskGroup.create_task fired link_tasks {len(recorded)} times, expected >= 3"


@pytest.mark.subprocess(err=None)
def test_set_event_loop_triggers_track_asyncio_loop() -> None:
    """``EventLoopPolicy.set_event_loop(loop)`` must invoke
    ``stack.track_asyncio_loop`` so the profiler knows about the loop.
    """
    import asyncio

    from tests.profiling.collector._asyncio_wrap_helpers import captured_link_calls
    from tests.profiling.collector._asyncio_wrap_helpers import started_profiler

    with started_profiler(), captured_link_calls("track_asyncio_loop") as recorded:
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            assert id(loop) in recorded, "set_event_loop did not fire track_asyncio_loop"
        finally:
            asyncio.set_event_loop(None)
            loop.close()


# ---------------------------------------------------------------------------
# Introspection metadata — the trampoline's (*args, **kwargs) shape must
# not leak through; downstream libraries (FastAPI, validators, …)
# introspect asyncio API signatures and break if we report something
# other than the real shape.
# ---------------------------------------------------------------------------


@pytest.mark.subprocess(err=None)
def test_wrap_preserves_inspect_signature() -> None:
    """``inspect.signature(asyncio.tasks.create_task)`` after profiler start
    must match the unwrapped signature.  The trampoline carries a
    ``(*args, **kwargs)`` shape; ``__wrapped__`` set on the original lets
    ``inspect.signature`` recover the real argument metadata.
    """
    import asyncio
    import inspect

    pre_sig = inspect.signature(asyncio.tasks.create_task)

    from tests.profiling.collector._asyncio_wrap_helpers import started_profiler

    with started_profiler():
        post_sig = inspect.signature(asyncio.tasks.create_task)
        assert str(post_sig) == str(pre_sig), f"signature regressed under wrap: {pre_sig} -> {post_sig}"
        assert hasattr(asyncio.tasks.create_task, "__wrapped__"), (
            "__wrapped__ must be set so inspect.signature() can recover the original signature"
        )


# ---------------------------------------------------------------------------
# Wrap gating: with stack profiling disabled, importing _asyncio must not
# mutate asyncio.tasks.create_task. The wrapping inside the ModuleWatchdog
# hook is gated on ``config.stack.enabled and stack.is_available`` (which
# defaults to True), so the only deterministic way to assert "no wrap" is
# to disable stack explicitly in a subprocess.
# ---------------------------------------------------------------------------


@pytest.mark.subprocess(err=None, env={"DD_PROFILING_STACK_ENABLED": "false"})
def test_module_import_with_stack_disabled_does_not_wrap_create_task() -> None:
    """With ``DD_PROFILING_STACK_ENABLED=false`` the asyncio ModuleWatchdog
    hook must run without retargeting ``asyncio.tasks.create_task`` (no
    ``__code__`` mutation, no attribute swap). Guards against a regression
    where the wrap escapes its ``init_stack`` gate.
    """
    import asyncio.tasks

    pre_code = asyncio.tasks.create_task.__code__
    pre_identity = asyncio.tasks.create_task

    import ddtrace.profiling._asyncio  # noqa: F401

    assert ddtrace.profiling._asyncio.ASYNCIO_IMPORTED, "ModuleWatchdog hook must have fired"
    assert asyncio.tasks.create_task is pre_identity, "create_task identity changed"
    assert asyncio.tasks.create_task.__code__ is pre_code, "create_task __code__ mutated"


@pytest.mark.subprocess(err=None)
def test_pre_cached_reference_still_triggers_callback() -> None:
    """The uvloop scenario: a library captures ``asyncio.tasks.create_task``
    at *its* import time, **before** the profiler starts. Calls made
    through that cached reference must still fire ``stack.weak_link_tasks``.

    This is the user-visible property that identity-preserving wrap gives
    us. A ``setattr``-style wrap would leave the cached reference pointing
    at the original (un-wrapped) function, the callback would never fire,
    and the C sampler would lose the parent-task link — exactly the
    regression that broke uvloop CI variants before this PR.
    """
    import asyncio

    # Capture BEFORE any ddtrace import. This is what uvloop's Cython
    # modules effectively do at their own module-init time.
    from asyncio.tasks import create_task as pre_cached_create_task

    from tests.profiling.collector._asyncio_wrap_helpers import captured_link_calls
    from tests.profiling.collector._asyncio_wrap_helpers import started_profiler

    with started_profiler(), captured_link_calls("weak_link_tasks") as recorded:

        async def child() -> None:
            await asyncio.sleep(0)

        async def main() -> int:
            # Call through the PRE-CACHED reference, not asyncio.create_task.
            task = pre_cached_create_task(child())
            await task
            return id(task)

        task_id = asyncio.run(main())

        assert task_id in recorded, (
            "create_task invoked through a reference cached before profiler "
            "start did not trigger stack.weak_link_tasks — identity-preserving "
            "wrap is broken"
        )


# ---------------------------------------------------------------------------
# Args / kwargs handling — guards against a class of bugs where the wrapper
# substitutes a value into ``args`` while the user passed it as a kwarg (or
# vice versa), producing ``TypeError: got multiple values for argument``.
# ---------------------------------------------------------------------------


@pytest.mark.subprocess(err=None)
def test_keyword_arg_form_does_not_double_substitute() -> None:
    """The ``shield``/``as_completed`` wrappers substitute a value back into
    the call. If they substituted via ``args`` when the caller used kwargs
    we'd get ``TypeError: got multiple values for argument``. Covers both
    APIs in one test.
    """
    import asyncio

    from tests.profiling.collector._asyncio_wrap_helpers import started_profiler

    with started_profiler():

        async def child(x: int) -> int:
            await asyncio.sleep(0)
            return x

        async def main_shield() -> int:
            return await asyncio.shield(arg=child(11))

        async def main_as_completed() -> list[int]:
            results: list[int] = []
            for fut in asyncio.as_completed(fs=[child(0), child(1), child(2)]):
                results.append(await fut)
            return sorted(results)

        assert asyncio.run(main_shield()) == 11
        assert asyncio.run(main_as_completed()) == [0, 1, 2]


# ---------------------------------------------------------------------------
# Return value / behaviour invariants — guards against the wrapper accidentally
# corrupting the API contract of the wrapped function.
# ---------------------------------------------------------------------------


@pytest.mark.subprocess(err=None)
def test_gather_with_return_exceptions_keeps_kwarg() -> None:
    """``asyncio.gather(..., return_exceptions=True)`` must return exceptions
    rather than raising. Verifies that the wrapper doesn't drop the kwarg.
    """
    import asyncio

    from tests.profiling.collector._asyncio_wrap_helpers import started_profiler

    with started_profiler():

        async def good() -> int:
            return 1

        async def bad() -> int:
            raise ValueError("boom")

        async def main() -> list[Any]:
            results = await asyncio.gather(good(), bad(), return_exceptions=True)
            return [type(r).__name__ if isinstance(r, BaseException) else r for r in results]

        assert asyncio.run(main()) == [1, "ValueError"]


@pytest.mark.subprocess(err=None)
def test_wait_returns_done_pending_tuple() -> None:
    """``asyncio.wait`` must still return a ``(done, pending)`` tuple."""
    import asyncio

    from tests.profiling.collector._asyncio_wrap_helpers import started_profiler

    with started_profiler():

        async def child(x: int) -> int:
            await asyncio.sleep(0)
            return x

        async def main() -> tuple[int, int]:
            t1 = asyncio.ensure_future(child(1))
            t2 = asyncio.ensure_future(child(2))
            done, pending = await asyncio.wait([t1, t2])
            return len(done), len(pending)

        n_done, n_pending = asyncio.run(main())
        assert n_done == 2
        assert n_pending == 0


# ---------------------------------------------------------------------------
# Edge cases — empty inputs and error paths must not blow up the wrappers.
# ---------------------------------------------------------------------------


@pytest.mark.subprocess(err=None)
def test_gather_empty_does_not_link() -> None:
    """``asyncio.gather()`` with no children must not crash and must not
    fire ``link_tasks`` (no children to link).
    """
    import asyncio

    from tests.profiling.collector._asyncio_wrap_helpers import captured_link_calls
    from tests.profiling.collector._asyncio_wrap_helpers import started_profiler

    with started_profiler(), captured_link_calls("link_tasks") as recorded:

        async def main() -> list[Any]:
            return await asyncio.gather()

        assert asyncio.run(main()) == []
        assert recorded == [], f"Empty gather fired link_tasks: {recorded}"


@pytest.mark.subprocess(err=None)
def test_create_task_propagates_exception() -> None:
    """If the wrapped coroutine raises, the exception must propagate via
    ``await task`` — the wrapper must not swallow it.
    """
    import asyncio

    from tests.profiling.collector._asyncio_wrap_helpers import started_profiler

    with started_profiler():

        async def child() -> int:
            raise RuntimeError("expected boom")

        async def main() -> str | None:
            t = asyncio.create_task(child())
            try:
                await t
            except RuntimeError as e:
                return str(e)
            return None

        assert asyncio.run(main()) == "expected boom"


# ---------------------------------------------------------------------------
# TaskGroup-specific edge cases (3.11+)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.version_info < (3, 11), reason="TaskGroup is Python 3.11+")
@pytest.mark.subprocess(err=None)
def test_taskgroup_exception_propagates_through_wrapper() -> None:
    """A child task raising under a TaskGroup must propagate as an
    ExceptionGroup-or-equivalent through the ``async with``. The wrapper
    must not swallow exceptions or alter the propagation contract.
    """
    import asyncio

    from tests.profiling.collector._asyncio_wrap_helpers import started_profiler

    with started_profiler():

        async def child_ok() -> int:
            await asyncio.sleep(0)
            return 1

        async def child_bad() -> int:
            raise ValueError("expected")

        async def main() -> list[tuple[str, str]]:
            try:
                async with asyncio.TaskGroup() as tg:  # type: ignore[attr-defined]
                    tg.create_task(child_ok())
                    tg.create_task(child_bad())
            except BaseException as outer:
                exc_strs: list[tuple[str, str]] = []

                def collect(e: BaseException) -> None:
                    sub_excs = getattr(e, "exceptions", None)
                    if sub_excs is not None:
                        for sub in sub_excs:
                            collect(sub)
                    else:
                        exc_strs.append((type(e).__name__, str(e)))

                collect(outer)
                return exc_strs
            return []

        assert ("ValueError", "expected") in asyncio.run(main())


# ---------------------------------------------------------------------------
# Profiler lifecycle — wrapping must survive stop / start cycles.
# ---------------------------------------------------------------------------


@_SKIP_ON_UVLOOP
@pytest.mark.subprocess(err=None)
def test_wrapping_persists_across_profiler_restart() -> None:
    """The wrapping is installed once on first ``Profiler.start()`` (via the
    asyncio ModuleWatchdog hook) and persists for the rest of the process.
    Stopping and restarting the profiler must not break it.
    """
    import asyncio

    from ddtrace.profiling import profiler
    from tests.profiling.collector._asyncio_wrap_helpers import captured_link_calls

    # First cycle: start + stop.
    p = profiler.Profiler()
    p.start()
    p.stop()

    # Second cycle: wraps should still be in place.
    p2 = profiler.Profiler()
    p2.start()
    try:
        with captured_link_calls("weak_link_tasks") as recorded:

            async def child() -> None:
                await asyncio.sleep(0)

            async def main() -> int:
                t = asyncio.create_task(child())
                await t
                return id(t)

            task_id = asyncio.run(main())
            assert task_id in recorded, "Wrapping did not survive profiler restart"
    finally:
        p2.stop()


# Silence unused-import warnings on older Pythons that skip TaskGroup tests.
_ = sys
