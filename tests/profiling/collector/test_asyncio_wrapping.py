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
# Identity / metadata invariants
# ---------------------------------------------------------------------------


@pytest.mark.subprocess(err=None)
def test_alias_identity_preserved_after_wrapping() -> None:
    """``asyncio.X`` and ``asyncio.tasks.X`` must remain the same function
    object after the profiler wraps the asyncio internals.

    This guards against a regression where setattr-style wrapping replaces
    only one of two aliased bindings, leaving user code that calls
    ``asyncio.create_task(...)`` unobserved.
    """
    import asyncio
    import asyncio.tasks  # noqa: F401

    from ddtrace.profiling import profiler

    p = profiler.Profiler()
    p.start()
    try:
        assert asyncio.create_task is asyncio.tasks.create_task
        assert asyncio.shield is asyncio.tasks.shield
        assert asyncio.as_completed is asyncio.tasks.as_completed
    finally:
        p.stop()


@pytest.mark.subprocess(err=None)
def test_function_metadata_preserved_after_wrapping() -> None:
    """The wrapped callables must keep ``__name__`` / ``__module__`` so
    stack frames and debug output keep referring to ``asyncio.tasks.create_task``
    rather than a nameless trampoline.
    """
    import asyncio

    from ddtrace.profiling import profiler

    p = profiler.Profiler()
    p.start()
    try:
        assert asyncio.create_task.__name__ == "create_task"
        assert asyncio.create_task.__module__ == "asyncio.tasks"
        assert asyncio.shield.__name__ == "shield"
        assert asyncio.as_completed.__name__ == "as_completed"
    finally:
        p.stop()


# ---------------------------------------------------------------------------
# Callback-firing invariants
# ---------------------------------------------------------------------------


@_SKIP_ON_UVLOOP
@pytest.mark.subprocess(err=None)
def test_create_task_via_both_bindings_triggers_callback() -> None:
    """``asyncio.create_task(...)`` and ``asyncio.tasks.create_task(...)`` must
    each fire ``stack.weak_link_tasks``. This is the alias case: a setattr
    replacement that only patched one of the two bindings would silently miss
    user code that uses the package-level alias.
    """
    import asyncio

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler

    p = profiler.Profiler()
    p.start()
    try:
        recorded: list[tuple[int, int]] = []
        original_weak_link = stack.weak_link_tasks

        def recorder(parent, child) -> None:
            recorded.append((id(parent), id(child)))
            return original_weak_link(parent, child)

        stack.weak_link_tasks = recorder

        async def child() -> None:
            await asyncio.sleep(0)

        async def main():
            via_alias = asyncio.create_task(child())
            via_canonical = asyncio.tasks.create_task(child())
            await via_alias
            await via_canonical
            return id(via_alias), id(via_canonical)

        alias_id, canonical_id = asyncio.run(main())

        recorded_child_ids = {child_id for _, child_id in recorded}
        assert alias_id in recorded_child_ids, (
            "asyncio.create_task did not trigger weak_link_tasks; alias binding may be unwrapped"
        )
        assert canonical_id in recorded_child_ids, "asyncio.tasks.create_task did not trigger weak_link_tasks"
    finally:
        p.stop()


@pytest.mark.subprocess(err=None)
def test_gather_triggers_link_tasks() -> None:
    """``asyncio.gather(...)`` must invoke ``stack.link_tasks`` for each child,
    via the wrapped ``_GatheringFuture.__init__``.
    """
    import asyncio

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler

    p = profiler.Profiler()
    p.start()
    try:
        recorded_children: list[int] = []
        original_link = stack.link_tasks

        def recorder(parent, child) -> None:
            recorded_children.append(id(child))
            return original_link(parent, child)

        stack.link_tasks = recorder

        async def child() -> int:
            await asyncio.sleep(0)
            return 1

        async def main():
            t1 = asyncio.ensure_future(child())
            t2 = asyncio.ensure_future(child())
            await asyncio.gather(t1, t2)
            return id(t1), id(t2)

        t1_id, t2_id = asyncio.run(main())

        assert t1_id in recorded_children, "gather did not link first child"
        assert t2_id in recorded_children, "gather did not link second child"
    finally:
        p.stop()


@pytest.mark.subprocess(err=None)
def test_shield_triggers_link_tasks() -> None:
    """``asyncio.shield(awaitable)`` must invoke ``stack.link_tasks`` for the
    shielded future. The wrapper additionally wraps the awaitable into a
    ``Future`` via ``ensure_future`` and substitutes it back into the call —
    we only check that link_tasks fires; the substitution correctness is
    covered by the existing ``test_asyncio_shield.py`` integration test.
    """
    import asyncio

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler

    p = profiler.Profiler()
    p.start()
    try:
        recorded: list[int] = []
        original_link = stack.link_tasks

        def recorder(parent, child) -> None:
            recorded.append(id(child))
            return original_link(parent, child)

        stack.link_tasks = recorder

        async def child() -> int:
            await asyncio.sleep(0)
            return 7

        async def main() -> int:
            return await asyncio.shield(child())

        result = asyncio.run(main())
        assert result == 7
        assert len(recorded) >= 1, "shield did not fire link_tasks"
    finally:
        p.stop()


@pytest.mark.subprocess(err=None)
def test_as_completed_triggers_link_tasks_per_child() -> None:
    """``asyncio.as_completed([c1, c2, c3])`` must invoke ``stack.link_tasks``
    once per coroutine.
    """
    import asyncio

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler

    p = profiler.Profiler()
    p.start()
    try:
        recorded: list[int] = []
        original_link = stack.link_tasks

        def recorder(parent, child) -> None:
            recorded.append(id(child))
            return original_link(parent, child)

        stack.link_tasks = recorder

        async def child(x: int) -> int:
            await asyncio.sleep(0)
            return x

        async def main() -> list[int]:
            coros = [child(i) for i in range(3)]
            results = []
            for fut in asyncio.as_completed(coros):
                results.append(await fut)
            return sorted(results)

        results = asyncio.run(main())
        assert results == [0, 1, 2]
        # as_completed wraps each coro into a Future via ensure_future and
        # links each one — so we expect at least 3 callbacks.
        assert len(recorded) >= 3, "as_completed fired link_tasks %d times, expected >= 3" % len(recorded)
    finally:
        p.stop()


@pytest.mark.subprocess(err=None)
def test_wait_triggers_link_tasks_per_future() -> None:
    """``asyncio.wait([t1, t2])`` calls ``asyncio.tasks._wait`` internally; the
    wrapper there must invoke ``stack.link_tasks`` once per future.
    """
    import asyncio

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler

    p = profiler.Profiler()
    p.start()
    try:
        recorded: list[int] = []
        original_link = stack.link_tasks

        def recorder(parent, child) -> None:
            recorded.append(id(child))
            return original_link(parent, child)

        stack.link_tasks = recorder

        async def child() -> None:
            await asyncio.sleep(0)

        async def main():
            t1 = asyncio.ensure_future(child())
            t2 = asyncio.ensure_future(child())
            await asyncio.wait([t1, t2])
            return id(t1), id(t2)

        t1_id, t2_id = asyncio.run(main())
        # wait may also fire gather-style link_tasks on the inner _GatheringFuture
        # — we only require both leaf futures show up.
        assert t1_id in recorded, "wait did not link first future"
        assert t2_id in recorded, "wait did not link second future"
    finally:
        p.stop()


@pytest.mark.skipif(sys.version_info < (3, 11), reason="TaskGroup is Python 3.11+")
@pytest.mark.subprocess(err=None)
def test_taskgroup_triggers_link_tasks() -> None:
    """``asyncio.TaskGroup().create_task(coro)`` must invoke
    ``stack.link_tasks`` for each task. TaskGroup is Python 3.11+ only.
    """
    import asyncio

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler

    p = profiler.Profiler()
    p.start()
    try:
        recorded: list[int] = []
        original_link = stack.link_tasks

        def recorder(parent, child) -> None:
            recorded.append(id(child))
            return original_link(parent, child)

        stack.link_tasks = recorder

        async def child(x: int) -> int:
            await asyncio.sleep(0)
            return x

        async def main() -> list[int]:
            results: list[int] = []
            # mypy doesn't know about TaskGroup on older type stubs and the
            # skipif gate above means this code only runs on 3.11+.
            async with asyncio.TaskGroup() as tg:  # type: ignore[attr-defined]
                tasks = [tg.create_task(child(i)) for i in range(3)]
            for t in tasks:
                results.append(t.result())
            return sorted(results)

        results = asyncio.run(main())
        assert results == [0, 1, 2]
        assert len(recorded) >= 3, "TaskGroup.create_task fired link_tasks %d times, expected >= 3" % len(recorded)
    finally:
        p.stop()


@pytest.mark.subprocess(err=None)
def test_set_event_loop_triggers_track_asyncio_loop() -> None:
    """``EventLoopPolicy.set_event_loop(loop)`` must invoke
    ``stack.track_asyncio_loop`` so the profiler knows about the loop.
    """
    import asyncio

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler

    p = profiler.Profiler()
    p.start()
    try:
        recorded: list[int] = []
        original_track = stack.track_asyncio_loop

        def recorder(thread_id, loop) -> None:
            if loop is not None:
                recorded.append(id(loop))
            return original_track(thread_id, loop)

        stack.track_asyncio_loop = recorder

        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            assert id(loop) in recorded, "set_event_loop did not fire track_asyncio_loop"
        finally:
            asyncio.set_event_loop(None)
            loop.close()
    finally:
        p.stop()


# ---------------------------------------------------------------------------
# Wrapping is in place even when the wrapper has no observable side-effect
# ---------------------------------------------------------------------------


@pytest.mark.subprocess(err=None)
def test_wrapped_call_returns_original_result() -> None:
    """The wrapper must transparently return the value of the original call.
    Belt-and-braces check that the wrapping does not corrupt return values.
    """
    import asyncio

    from ddtrace.profiling import profiler

    p = profiler.Profiler()
    p.start()
    try:

        async def child(x: int) -> int:
            await asyncio.sleep(0)
            return x * 2

        async def main():
            t = asyncio.create_task(child(21))
            return await t

        assert asyncio.run(main()) == 42
    finally:
        p.stop()


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

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler

    p = profiler.Profiler()
    p.start()
    try:
        recorded_child_ids: list[int] = []
        original_weak_link = stack.weak_link_tasks

        def recorder(parent, child) -> None:
            recorded_child_ids.append(id(child))
            return original_weak_link(parent, child)

        stack.weak_link_tasks = recorder

        async def child() -> None:
            await asyncio.sleep(0)

        async def main() -> int:
            # Call through the PRE-CACHED reference, not asyncio.create_task.
            task = pre_cached_create_task(child())
            await task
            return id(task)

        task_id = asyncio.run(main())

        assert task_id in recorded_child_ids, (
            "create_task invoked through a reference cached before profiler "
            "start did not trigger stack.weak_link_tasks — identity-preserving "
            "wrap is broken"
        )
    finally:
        p.stop()


# ---------------------------------------------------------------------------
# Args / kwargs handling — guards against a class of bugs where the wrapper
# substitutes a value into ``args`` while the user passed it as a kwarg (or
# vice versa), producing ``TypeError: got multiple values for argument``.
# ---------------------------------------------------------------------------


@pytest.mark.subprocess(err=None)
def test_shield_keyword_arg_form_does_not_raise() -> None:
    """``asyncio.shield(arg=fut)`` (keyword form) must work — the wrapper
    must not duplicate the substituted value into both args and kwargs.
    """
    import asyncio

    from ddtrace.profiling import profiler

    p = profiler.Profiler()
    p.start()
    try:

        async def child() -> int:
            await asyncio.sleep(0)
            return 11

        async def main() -> int:
            # Keyword form — not commonly used but valid.
            return await asyncio.shield(arg=child())

        result = asyncio.run(main())
        assert result == 11
    finally:
        p.stop()


@pytest.mark.subprocess(err=None)
def test_as_completed_keyword_arg_form_does_not_raise() -> None:
    """``asyncio.as_completed(fs=...)`` (keyword form) must work."""
    import asyncio

    from ddtrace.profiling import profiler

    p = profiler.Profiler()
    p.start()
    try:

        async def child(x: int) -> int:
            await asyncio.sleep(0)
            return x

        async def main() -> list[int]:
            results: list[int] = []
            for fut in asyncio.as_completed(fs=[child(0), child(1), child(2)]):
                results.append(await fut)
            return sorted(results)

        assert asyncio.run(main()) == [0, 1, 2]
    finally:
        p.stop()


# ---------------------------------------------------------------------------
# Return value / behaviour invariants — guards against the wrapper accidentally
# corrupting the API contract of the wrapped function.
# ---------------------------------------------------------------------------


@pytest.mark.subprocess(err=None)
def test_create_task_preserves_name_kwarg() -> None:
    """``create_task(coro, name='X')`` must produce a task named 'X'."""
    import asyncio

    from ddtrace.profiling import profiler

    p = profiler.Profiler()
    p.start()
    try:

        async def child() -> None:
            await asyncio.sleep(0)

        async def main() -> str:
            t = asyncio.create_task(child(), name="hello-world")
            await t
            return t.get_name()

        assert asyncio.run(main()) == "hello-world"
    finally:
        p.stop()


@pytest.mark.subprocess(err=None)
def test_gather_returns_results_in_order() -> None:
    """``asyncio.gather(c1, c2, c3)`` must return ``[r1, r2, r3]`` in order
    even after wrapping.
    """
    import asyncio

    from ddtrace.profiling import profiler

    p = profiler.Profiler()
    p.start()
    try:

        async def child(x: int) -> int:
            # Stagger completion so the ordering test is real.
            await asyncio.sleep((10 - x) * 0.001)
            return x

        async def main() -> list[int]:
            # asyncio.gather is typed as returning tuple[T1, T2, ...]; runtime is a list.
            return list(await asyncio.gather(child(1), child(2), child(3)))

        assert asyncio.run(main()) == [1, 2, 3]
    finally:
        p.stop()


@pytest.mark.subprocess(err=None)
def test_gather_with_return_exceptions_keeps_kwarg() -> None:
    """``asyncio.gather(..., return_exceptions=True)`` must return exceptions
    rather than raising. Verifies that the wrapper doesn't drop the kwarg.
    """
    import asyncio

    from ddtrace.profiling import profiler

    p = profiler.Profiler()
    p.start()
    try:

        async def good() -> int:
            return 1

        async def bad() -> int:
            raise ValueError("boom")

        async def main():
            results = await asyncio.gather(good(), bad(), return_exceptions=True)
            return [type(r).__name__ if isinstance(r, BaseException) else r for r in results]

        assert asyncio.run(main()) == [1, "ValueError"]
    finally:
        p.stop()


@pytest.mark.subprocess(err=None)
def test_wait_returns_done_pending_tuple() -> None:
    """``asyncio.wait`` must still return a ``(done, pending)`` tuple."""
    import asyncio

    from ddtrace.profiling import profiler

    p = profiler.Profiler()
    p.start()
    try:

        async def child(x: int) -> int:
            await asyncio.sleep(0)
            return x

        async def main():
            t1 = asyncio.ensure_future(child(1))
            t2 = asyncio.ensure_future(child(2))
            done, pending = await asyncio.wait([t1, t2])
            return len(done), len(pending)

        n_done, n_pending = asyncio.run(main())
        assert n_done == 2
        assert n_pending == 0
    finally:
        p.stop()


@pytest.mark.subprocess(err=None)
def test_shield_returns_underlying_result() -> None:
    """``await asyncio.shield(coro)`` must yield the coroutine's return value."""
    import asyncio

    from ddtrace.profiling import profiler

    p = profiler.Profiler()
    p.start()
    try:

        async def child() -> str:
            await asyncio.sleep(0)
            return "shielded-value"

        async def main() -> str:
            return await asyncio.shield(child())

        assert asyncio.run(main()) == "shielded-value"
    finally:
        p.stop()


# ---------------------------------------------------------------------------
# Edge cases — empty inputs and error paths must not blow up the wrappers.
# ---------------------------------------------------------------------------


@pytest.mark.subprocess(err=None)
def test_gather_empty_does_not_link() -> None:
    """``asyncio.gather()`` with no children must not crash and must not
    fire ``link_tasks`` (no children to link).
    """
    import asyncio

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler

    p = profiler.Profiler()
    p.start()
    try:
        recorded: list[int] = []
        original_link = stack.link_tasks

        def recorder(parent, child) -> None:
            recorded.append(id(child))
            return original_link(parent, child)

        stack.link_tasks = recorder

        async def main():
            return await asyncio.gather()

        result = asyncio.run(main())
        assert result == []
        # Empty gather → no children → no link_tasks calls
        assert recorded == [], f"Empty gather fired link_tasks: {recorded}"
    finally:
        p.stop()


@pytest.mark.subprocess(err=None)
def test_as_completed_empty_iterator() -> None:
    """``asyncio.as_completed([])`` must yield nothing and not crash."""
    import asyncio

    from ddtrace.profiling import profiler

    p = profiler.Profiler()
    p.start()
    try:

        async def main() -> list[int]:
            results: list[int] = []
            empty: list[asyncio.Future[int]] = []
            for fut in asyncio.as_completed(empty):
                results.append(await fut)
            return results

        assert asyncio.run(main()) == []
    finally:
        p.stop()


@pytest.mark.subprocess(err=None)
def test_create_task_propagates_exception() -> None:
    """If the wrapped coroutine raises, the exception must propagate via
    ``await task`` — the wrapper must not swallow it.
    """
    import asyncio

    from ddtrace.profiling import profiler

    p = profiler.Profiler()
    p.start()
    try:

        async def child() -> int:
            raise RuntimeError("expected boom")

        async def main():
            t = asyncio.create_task(child())
            try:
                await t
            except RuntimeError as e:
                return str(e)
            return None

        assert asyncio.run(main()) == "expected boom"
    finally:
        p.stop()


@pytest.mark.subprocess(err=None)
def test_wrapped_create_task_returns_real_task_object() -> None:
    """``asyncio.create_task(coro)`` must return a genuine ``asyncio.Task``
    (not a wrapper / proxy). Some downstream code calls ``Task``-specific
    methods on the result.
    """
    import asyncio

    from ddtrace.profiling import profiler

    p = profiler.Profiler()
    p.start()
    try:

        async def child() -> None:
            await asyncio.sleep(0)

        async def main():
            t = asyncio.create_task(child())
            try:
                # Access a Task-specific method
                assert isinstance(t, asyncio.Task)
                assert hasattr(t, "get_name")
                assert hasattr(t, "cancel")
            finally:
                await t
            return True

        assert asyncio.run(main()) is True
    finally:
        p.stop()


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

    from ddtrace.profiling import profiler

    p = profiler.Profiler()
    p.start()
    try:

        async def child_ok() -> int:
            await asyncio.sleep(0)
            return 1

        async def child_bad() -> int:
            raise ValueError("expected")

        async def main():
            try:
                async with asyncio.TaskGroup() as tg:
                    tg.create_task(child_ok())
                    tg.create_task(child_bad())
            except BaseException as outer:
                # Could be ExceptionGroup (3.11+) or BaseExceptionGroup;
                # check the structure includes our ValueError.
                exc_strs = []

                def collect(e):
                    if hasattr(e, "exceptions"):
                        for sub in e.exceptions:
                            collect(sub)
                    else:
                        exc_strs.append((type(e).__name__, str(e)))

                collect(outer)
                return exc_strs
            return []

        result = asyncio.run(main())
        assert ("ValueError", "expected") in result, f"Unexpected: {result}"
    finally:
        p.stop()


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

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler

    p = profiler.Profiler()
    p.start()
    p.stop()

    p2 = profiler.Profiler()
    p2.start()
    try:
        recorded: list[int] = []
        original = stack.weak_link_tasks

        def recorder(parent, child) -> None:
            recorded.append(id(child))
            return original(parent, child)

        stack.weak_link_tasks = recorder

        async def child() -> None:
            await asyncio.sleep(0)

        async def main():
            t = asyncio.create_task(child())
            await t
            return id(t)

        task_id = asyncio.run(main())
        assert task_id in recorded, "Wrapping did not survive profiler restart"
    finally:
        p2.stop()


# Silence unused-import warnings on older Pythons that skip TaskGroup tests.
_ = sys
