"""Validation of ``ddtrace.aiguard._context``.

The _context module owns AI-Guard collision avoidance: a framework
integration (LangChain, Strands) claims the phases of a model call it
evaluates itself, and provider-level integrations (OpenAI, Anthropic) skip
only the phase they are asked about. Each context holds a stack of
per-phase claim objects in a ContextVar. Isolation across threads and
asyncio tasks is provided by Python's standard Context propagation, and
because copied contexts share the claim objects, a release from another
task is seen by the task that claimed.

Coverage:

* Owner isolation — threads and asyncio sibling tasks don't see each
  other's claims, courtesy of ContextVar's per-thread / per-task
  inheritance via ``copy_context()``.
* Nesting — claims stack; an inner reset leaves the outer claim active.
* ``aiguard_context()`` — set/reset around a block, including exception
  paths.
* Phase scoping — claiming one phase leaves the other free.
* Cross-context release — a token released from another asyncio task
  neither raises, leaves the claiming task covered, nor drops an unrelated
  claim (APPSEC-70282), including a LangChain stream finalized by another task.
"""

import asyncio
import threading

import pytest

from ddtrace.aiguard._context import Phase
from ddtrace.aiguard._context import aiguard_context
from ddtrace.aiguard._context import is_aiguard_context_active
from ddtrace.aiguard._context import reset_aiguard_context_active
from ddtrace.aiguard._context import reset_aiguard_context_active_current
from ddtrace.aiguard._context import set_aiguard_context_active
from ddtrace.aiguard.integrations._langchain import _langchain_stream_finally
from ddtrace.aiguard.integrations._langchain import _langchain_stream_started


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
        Context for each task, so the framework task's claim is invisible
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

    def test_many_concurrent_workers_do_not_corrupt_state(self):
        """Stress: N threads all set+reset concurrently. No active state may leak."""
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
# Nesting
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

    def test_reset_none_is_safe(self):
        """Defensive: ``reset_aiguard_context_active(None)`` MUST be a no-op."""
        reset_aiguard_context_active(None)
        assert is_aiguard_context_active() is False

    def test_reset_current_with_no_active_set_is_safe(self):
        """Tokenless reset MUST be a no-op when nothing is active.

        Pinned because the .after listener may fire without a matching
        .before if dispatch is reconfigured at runtime.
        """
        assert is_aiguard_context_active() is False
        reset_aiguard_context_active_current()
        reset_aiguard_context_active_current()
        assert is_aiguard_context_active() is False

    def test_reset_current_releases_one_level(self):
        """.after listener pattern: .before sets, .after calls the tokenless reset."""
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
        both run in the same task, so the claim is balanced.
        """
        assert is_aiguard_context_active() is False
        with aiguard_context():
            await asyncio.sleep(0)
            assert is_aiguard_context_active() is True
        assert is_aiguard_context_active() is False


# ---------------------------------------------------------------------------
# Phase scoping (APPSEC-70286)
#
# A framework claims only the phases it evaluates itself. The all-or-nothing
# flag this replaced made LangChain streaming suppress the provider's
# buffered-stream evaluation without providing one, so a streamed response was
# scanned by nobody.
# ---------------------------------------------------------------------------


class TestPhases:
    def test_claiming_one_phase_leaves_the_other_free(self):
        token = set_aiguard_context_active(Phase.REQUEST)
        try:
            assert is_aiguard_context_active(Phase.REQUEST) is True
            assert is_aiguard_context_active(Phase.RESPONSE) is False
            # The phase-less form still reports that *something* is in flight.
            assert is_aiguard_context_active() is True
        finally:
            reset_aiguard_context_active(token)
        assert is_aiguard_context_active() is False

    def test_no_argument_claims_every_phase(self):
        token = set_aiguard_context_active()
        try:
            assert is_aiguard_context_active(Phase.REQUEST) is True
            assert is_aiguard_context_active(Phase.RESPONSE) is True
        finally:
            reset_aiguard_context_active(token)
        assert is_aiguard_context_active(Phase.REQUEST) is False
        assert is_aiguard_context_active(Phase.RESPONSE) is False

    def test_reset_releases_only_what_was_claimed(self):
        """Releasing a request-phase claim MUST NOT disturb a separate response claim."""
        response_token = set_aiguard_context_active(Phase.RESPONSE)
        request_token = set_aiguard_context_active(Phase.REQUEST)
        try:
            reset_aiguard_context_active(request_token)
            assert is_aiguard_context_active(Phase.REQUEST) is False
            assert is_aiguard_context_active(Phase.RESPONSE) is True
        finally:
            reset_aiguard_context_active(response_token)
        assert is_aiguard_context_active() is False

    def test_tokenless_reset_is_phase_scoped(self):
        set_aiguard_context_active(Phase.REQUEST, Phase.RESPONSE)
        try:
            reset_aiguard_context_active_current(Phase.REQUEST)
            assert is_aiguard_context_active(Phase.REQUEST) is False
            assert is_aiguard_context_active(Phase.RESPONSE) is True
        finally:
            reset_aiguard_context_active_current(Phase.RESPONSE)
        assert is_aiguard_context_active() is False

    def test_context_manager_is_phase_scoped(self):
        with aiguard_context(Phase.RESPONSE):
            assert is_aiguard_context_active(Phase.RESPONSE) is True
            assert is_aiguard_context_active(Phase.REQUEST) is False
        assert is_aiguard_context_active() is False


# ---------------------------------------------------------------------------
# Cross-context release (APPSEC-70282)
#
# Strands stores the before-invocation token on invocation_state and releases
# it in after-invocation, from the finally of an async generator. A consumer
# can advance or close that generator from another asyncio task, which works
# on a copy of the claiming task's Context.
# ---------------------------------------------------------------------------


class TestCrossContextRelease:
    @pytest.mark.asyncio
    async def test_token_reset_from_foreign_context_does_not_raise(self):
        """ContextVar.reset would raise ValueError straight into the framework's cleanup path."""
        box = {}

        async def _before():
            box["token"] = set_aiguard_context_active(Phase.REQUEST, Phase.RESPONSE)

        async def _after():
            reset_aiguard_context_active(box["token"])

        await asyncio.create_task(_before())
        await asyncio.create_task(_after())  # must not raise
        assert is_aiguard_context_active() is False

    @pytest.mark.asyncio
    async def test_release_from_child_task_clears_claiming_task(self):
        """A leaked claim would make every later provider call in this task skip AI Guard."""
        token = set_aiguard_context_active(Phase.REQUEST, Phase.RESPONSE)

        async def _release():
            reset_aiguard_context_active(token)

        await asyncio.create_task(_release())
        assert is_aiguard_context_active(Phase.REQUEST) is False
        assert is_aiguard_context_active(Phase.RESPONSE) is False

    @pytest.mark.asyncio
    async def test_foreign_release_keeps_unrelated_claim(self):
        """Releasing a claim this context never saw MUST NOT drop the claim it does hold."""

        async def _claim():
            return set_aiguard_context_active(Phase.REQUEST, Phase.RESPONSE)

        foreign = await asyncio.create_task(_claim())
        own = set_aiguard_context_active(Phase.REQUEST, Phase.RESPONSE)
        try:
            reset_aiguard_context_active(foreign)
            assert is_aiguard_context_active(Phase.REQUEST) is True
            assert is_aiguard_context_active(Phase.RESPONSE) is True
        finally:
            reset_aiguard_context_active(own)
        assert is_aiguard_context_active() is False

    @pytest.mark.asyncio
    async def test_generator_closed_from_another_task_releases_claim(self):
        """The Strands shape: claim before a generator's try, release in its finally.

        Breaking out early and closing the generator from another task -- as
        asyncio's async-generator finalizer does -- must still release the claim.
        """

        async def _run_loop():
            token = set_aiguard_context_active(Phase.REQUEST, Phase.RESPONSE)
            try:
                yield "first"
                yield "second"
            finally:
                reset_aiguard_context_active(token)

        agen = _run_loop()
        assert await agen.__anext__() == "first"
        assert is_aiguard_context_active() is True
        await asyncio.create_task(agen.aclose())
        assert is_aiguard_context_active() is False

    @pytest.mark.asyncio
    async def test_langchain_stream_finalized_by_preexisting_task_releases_claim(self):
        """The LangChain stream shape: .stream.started and .stream.finally share a per-stream state.

        The finalizing task predates the claim, so its Context never saw it; a
        tokenless release there would leave the starter covered for good.
        """
        state: dict = {}
        finalize = asyncio.Event()

        async def _finalizer():
            await finalize.wait()
            _langchain_stream_finally(state)

        finalizer = asyncio.create_task(_finalizer())
        await asyncio.sleep(0)  # let the finalizer copy the Context before the claim

        _langchain_stream_started(state)
        assert is_aiguard_context_active(Phase.REQUEST) is True
        assert is_aiguard_context_active(Phase.RESPONSE) is False

        finalize.set()
        await finalizer
        assert is_aiguard_context_active() is False
