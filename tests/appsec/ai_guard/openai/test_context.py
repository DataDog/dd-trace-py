"""Deep validation of ``ddtrace.appsec._ai_guard._context``.

The ``_context`` module owns AI-Guard collision avoidance: framework
integrations (LangChain, Strands) flip an "evaluation in progress" flag
so provider-level integrations (OpenAI) skip their own evaluation. The
flag is tracked by a per-owner (asyncio task / thread) counter — the
trade-offs and failure modes covered here are exactly what the
implementation comment in ``_context.py`` lists, so a future refactor
that breaks one of these properties fails loudly.

Coverage:

* Owner isolation — threads and asyncio sibling tasks don't see each
  other's flag.
* Counter nesting — set/reset pairs increment/decrement the same
  counter; intermediate resets restore outer state.
* Cross-context reset — a reset that runs inside ``copy_context().run``
  (the LangChain stream finalize pattern) still clears the parent's
  flag. This is the primary regression target.
* Cross-thread reset (weakref-finalize-style) — a reset issued from a
  thread other than the original owner still decrements the original
  owner's counter via the token.
* ``AIGuardStreamGuard`` — idempotent reset, parent-clearing under
  ``ctx.run``, and the realistic terminal-event-fires-once contract.
* ``aiguard_context()`` — set/reset around a block, including exception
  paths.
"""

import asyncio
import contextvars
import threading

import pytest

from ddtrace.appsec._ai_guard._context import AIGuardStreamGuard
from ddtrace.appsec._ai_guard._context import aiguard_context
from ddtrace.appsec._ai_guard._context import is_aiguard_context_active
from ddtrace.appsec._ai_guard._context import reset_aiguard_context_active
from ddtrace.appsec._ai_guard._context import set_aiguard_context_active


# ---------------------------------------------------------------------------
# Owner isolation
# ---------------------------------------------------------------------------


class TestOwnerIsolation:
    def test_threads_have_independent_state(self):
        """Worker thread sets True; main thread MUST observe False."""
        worker_set = threading.Event()
        main_observed = threading.Event()
        main_view: list[bool] = []

        def _worker():
            token = set_aiguard_context_active()
            try:
                worker_set.set()
                main_observed.wait(timeout=2)
            finally:
                reset_aiguard_context_active(token)

        t = threading.Thread(target=_worker)
        t.start()
        try:
            assert worker_set.wait(timeout=2)
            main_view.append(is_aiguard_context_active())
        finally:
            main_observed.set()
            t.join(timeout=2)

        assert main_view == [False]
        assert is_aiguard_context_active() is False

    @pytest.mark.asyncio
    async def test_async_sibling_tasks_have_independent_state(self):
        """Two sibling asyncio tasks: one sets True, the other MUST observe False."""
        framework_started = asyncio.Event()
        release = asyncio.Event()
        provider_view: list[bool] = []

        async def _framework():
            token = set_aiguard_context_active()
            try:
                framework_started.set()
                await release.wait()
            finally:
                reset_aiguard_context_active(token)

        async def _provider():
            await framework_started.wait()
            try:
                provider_view.append(is_aiguard_context_active())
            finally:
                release.set()

        await asyncio.gather(_framework(), _provider())
        assert provider_view == [False]
        assert is_aiguard_context_active() is False

    def test_many_concurrent_workers_do_not_corrupt_counter(self):
        """Stress: N threads all set+reset concurrently. Final state MUST be 0
        (no key leaks, no counter underflow).
        """
        N = 50
        view_lock = threading.Lock()
        observed_during_active: list[bool] = []
        ready = threading.Barrier(N + 1)

        def _worker():
            ready.wait(timeout=5)
            token = set_aiguard_context_active()
            with view_lock:
                observed_during_active.append(is_aiguard_context_active())
            reset_aiguard_context_active(token)

        workers = [threading.Thread(target=_worker) for _ in range(N)]
        for w in workers:
            w.start()
        ready.wait(timeout=5)
        for w in workers:
            w.join(timeout=5)

        assert observed_during_active == [True] * N
        assert is_aiguard_context_active() is False


# ---------------------------------------------------------------------------
# Nesting (counter semantics)
# ---------------------------------------------------------------------------


class TestNesting:
    def test_inner_reset_keeps_outer_active(self):
        """LIFO-style nested set/reset: inner reset MUST NOT clear the outer."""
        outer = set_aiguard_context_active()
        try:
            inner = set_aiguard_context_active()
            assert is_aiguard_context_active() is True
            reset_aiguard_context_active(inner)
            assert is_aiguard_context_active() is True
        finally:
            reset_aiguard_context_active(outer)
        assert is_aiguard_context_active() is False

    def test_resets_in_any_order_still_clear(self):
        """Counter doesn't enforce LIFO order — outer-first reset is also valid
        as long as both halves of every set/reset pair fire.
        """
        a = set_aiguard_context_active()
        b = set_aiguard_context_active()
        reset_aiguard_context_active(a)
        assert is_aiguard_context_active() is True
        reset_aiguard_context_active(b)
        assert is_aiguard_context_active() is False

    def test_extra_reset_is_safe_noop(self):
        """A reset with no matching active set MUST NOT raise or underflow.

        Defensive callers (idempotent finalizers, weakref.finalize firing
        after a normal reset) may issue an extra reset.
        """
        token = set_aiguard_context_active()
        reset_aiguard_context_active(token)
        # Extra reset against an already-cleared owner: safe.
        reset_aiguard_context_active(token)
        assert is_aiguard_context_active() is False

    def test_reset_none_is_safe(self):
        """Defensive: ``reset_aiguard_context_active(None)`` MUST be a no-op."""
        reset_aiguard_context_active(None)
        assert is_aiguard_context_active() is False


# ---------------------------------------------------------------------------
# Cross-context reset (the LangChain stream finalize regression)
# ---------------------------------------------------------------------------


class TestCrossContextReset:
    def test_reset_in_copied_context_clears_parent(self):
        """LangChain stream pattern: ``set`` runs in the outer Context, ``reset``
        runs inside ``copy_context().run(...)``. Parent MUST observe False.

        This is the regression test for the cross-context leak — under the
        previous ``contextvars.ContextVar`` design the parent retained
        ``active=True`` forever because ``ContextVar.reset`` is bound to the
        originating Context and could not reach back into it.
        """
        token = set_aiguard_context_active()
        cleanup_needed = True
        try:
            ctx = contextvars.copy_context()
            ctx.run(reset_aiguard_context_active, token)
            assert is_aiguard_context_active() is False
            cleanup_needed = False
        finally:
            if cleanup_needed:
                reset_aiguard_context_active(token)
        assert is_aiguard_context_active() is False

    def test_stream_guard_reset_in_copied_context_clears_parent(self):
        """``AIGuardStreamGuard.reset()`` is the production entry point for the
        regression: ``_on_span_finished`` (and the weakref finalizer) typically
        fire inside ``context.run(...)``. The fix MUST clear the parent's flag.
        """
        guard = AIGuardStreamGuard()
        try:
            assert is_aiguard_context_active() is True

            ctx = contextvars.copy_context()
            ctx.run(guard.reset)

            assert is_aiguard_context_active() is False
        finally:
            guard.reset()  # idempotent — guard already done

    def test_stream_guard_reset_in_other_thread_clears_original_owner(self):
        """Mimics ``weakref.finalize`` firing during GC on a different thread.

        The token recorded at ``__init__`` carries the *original* owner, so a
        ``reset`` issued from any thread / task decrements the original
        owner's counter. The original-owner thread observes False afterwards.
        """
        guard = AIGuardStreamGuard()
        try:
            assert is_aiguard_context_active() is True

            done = threading.Event()
            error: list[BaseException] = []

            def _finalize():
                try:
                    guard.reset()
                finally:
                    done.set()

            t = threading.Thread(target=_finalize)
            t.start()
            assert done.wait(timeout=2)
            t.join(timeout=2)
            assert not error

            # Original (calling) thread observes the cleared state.
            assert is_aiguard_context_active() is False
        finally:
            guard.reset()


# ---------------------------------------------------------------------------
# AIGuardStreamGuard semantics
# ---------------------------------------------------------------------------


class TestStreamGuard:
    def test_reset_is_idempotent(self):
        """Stream finalization may fire multiple terminal events (exhaustion,
        error, generator-close, weakref-finalize). Only the first ``reset()``
        takes effect; further calls are no-ops.
        """
        guard = AIGuardStreamGuard()
        assert is_aiguard_context_active() is True
        guard.reset()
        assert is_aiguard_context_active() is False
        guard.reset()
        guard.reset()
        assert is_aiguard_context_active() is False

    def test_concurrent_guards_in_same_owner_track_independently(self):
        """Two ``AIGuardStreamGuard`` instances created back-to-back in the
        same owner increment the counter to 2; resetting one keeps active=True.
        """
        g1 = AIGuardStreamGuard()
        g2 = AIGuardStreamGuard()
        try:
            assert is_aiguard_context_active() is True
            g1.reset()
            # g2 still active — outer guard's counter still has 1 left.
            assert is_aiguard_context_active() is True
            g2.reset()
            assert is_aiguard_context_active() is False
        finally:
            g1.reset()
            g2.reset()


# ---------------------------------------------------------------------------
# aiguard_context() context manager
# ---------------------------------------------------------------------------


class TestAIGuardContextManager:
    def test_block_sets_and_clears(self):
        assert is_aiguard_context_active() is False
        with aiguard_context():
            assert is_aiguard_context_active() is True
        assert is_aiguard_context_active() is False

    def test_block_resets_on_exception(self):
        """``aiguard_context()`` MUST reset the flag even if the block raises —
        otherwise a single block-handled error would leak ``active=True`` for
        the rest of the task and silently disable provider-level evaluation.
        """
        assert is_aiguard_context_active() is False
        with pytest.raises(RuntimeError, match="boom"):
            with aiguard_context():
                assert is_aiguard_context_active() is True
                raise RuntimeError("boom")
        assert is_aiguard_context_active() is False

    def test_nested_blocks(self):
        with aiguard_context():
            assert is_aiguard_context_active() is True
            with aiguard_context():
                assert is_aiguard_context_active() is True
            # Inner exit MUST NOT clear outer.
            assert is_aiguard_context_active() is True
        assert is_aiguard_context_active() is False

    @pytest.mark.asyncio
    async def test_block_works_inside_asyncio_task(self):
        """The context manager works inside an asyncio task — set and reset
        both run in the same task, so the counter is balanced.
        """
        assert is_aiguard_context_active() is False
        with aiguard_context():
            await asyncio.sleep(0)
            assert is_aiguard_context_active() is True
        assert is_aiguard_context_active() is False
