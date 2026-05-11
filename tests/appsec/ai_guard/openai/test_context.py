"""Validation of ``ddtrace.appsec._ai_guard._context``.

The ``_context`` module owns AI-Guard collision avoidance: framework
integrations (LangChain, Strands) flip an "evaluation in progress" flag
so provider-level integrations (OpenAI) skip their own evaluation. The
flag is tracked by a single ``contextvars.ContextVar[int]`` depth counter,
matching the IAST request-context pattern. Isolation across threads and
asyncio tasks is provided by Python's standard Context propagation.

Coverage:

* Owner isolation — threads and asyncio sibling tasks don't see each
  other's flag, courtesy of ``ContextVar``'s per-thread / per-task
  inheritance via ``copy_context()``.
* Counter nesting — set/reset pairs increment/decrement the same
  counter; LIFO inner reset restores the outer state.
* ``aiguard_context()`` — set/reset around a block, including exception
  paths.
"""

import asyncio
import threading

import pytest

from ddtrace.appsec._ai_guard._context import aiguard_context
from ddtrace.appsec._ai_guard._context import is_aiguard_context_active
from ddtrace.appsec._ai_guard._context import reset_aiguard_context_active
from ddtrace.appsec._ai_guard._context import reset_aiguard_context_active_current
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
        """Two sibling asyncio tasks: one sets True, the other MUST observe False.

        ``asyncio.create_task`` (used by ``asyncio.gather``) copies the current
        Context for each task, so the framework task's depth bump is invisible
        to the provider task running concurrently.
        """
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
        (no counter underflow, no leaked active state).
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
        """LIFO-style nested set/reset: inner reset MUST NOT clear the outer.

        ``ContextVar.reset(token)`` restores the depth to the value recorded
        when the token's matching ``set`` ran — so the inner reset returns
        the depth from 2 to 1, not 0.
        """
        outer = set_aiguard_context_active()
        try:
            inner = set_aiguard_context_active()
            assert is_aiguard_context_active() is True
            reset_aiguard_context_active(inner)
            assert is_aiguard_context_active() is True
        finally:
            reset_aiguard_context_active(outer)
        assert is_aiguard_context_active() is False

    def test_reset_none_is_safe(self):
        """Defensive: ``reset_aiguard_context_active(None)`` MUST be a no-op."""
        reset_aiguard_context_active(None)
        assert is_aiguard_context_active() is False

    def test_reset_current_with_no_active_set_is_safe(self):
        """Tokenless reset MUST be a no-op when nothing is active.

        Pinned because the ``.after`` listener may fire without a matching
        ``.before`` if dispatch is reconfigured at runtime, and an underflow
        would surface as a negative depth that ``is_active`` would still
        report as False but that subsequent ``reset_current`` calls would
        compound.
        """
        assert is_aiguard_context_active() is False
        reset_aiguard_context_active_current()
        reset_aiguard_context_active_current()
        assert is_aiguard_context_active() is False

    def test_reset_current_decrements_one_level(self):
        """``.after`` listener pattern: ``.before`` set, ``.after`` calls
        tokenless reset — depth returns to 0.
        """
        token = set_aiguard_context_active()
        assert is_aiguard_context_active() is True
        try:
            reset_aiguard_context_active_current()
            assert is_aiguard_context_active() is False
            token = None
        finally:
            if token is not None:
                reset_aiguard_context_active(token)


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
