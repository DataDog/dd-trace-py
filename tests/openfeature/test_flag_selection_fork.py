"""Flag selection in forked children: the claim, inherited lifecycle bookkeeping, once-per-process diagnostics."""

import threading
from unittest import mock

import pytest

from ddtrace.internal.openfeature import _flag_selection
from ddtrace.internal.openfeature._flag_selection import canonicalize_flag_selection
from ddtrace.internal.openfeature._flag_selection import claim_process_selection
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE
from tests.openfeature.flag_selection_helpers import _create_source
from tests.openfeature.flag_selection_helpers import _FakeResponse
from tests.openfeature.flag_selection_helpers import _provider
from tests.openfeature.flag_selection_helpers import _ufc_body


# err=None: CPython 3.12+ warns on stderr when a multi-threaded process forks; the child's exit status is the result.
@pytest.mark.subprocess(err=None)
def test_once_per_process_guards_reset_in_a_forked_child():
    import os
    from unittest import mock

    from ddtrace.internal.openfeature import _flag_selection
    from ddtrace.internal.openfeature._flag_selection import canonicalize_flag_selection
    from ddtrace.internal.openfeature._flag_selection import claim_process_selection
    from ddtrace.internal.openfeature._flag_selection import report_ignored
    from ddtrace.internal.openfeature._flag_selection import selection_request_url
    from ddtrace.internal.openfeature._flag_selection import warn_remote_config

    endpoint = "https://example.test/config"
    claimed = canonicalize_flag_selection({"tags": ["a"]})
    with (
        mock.patch.object(_flag_selection, "log"),
        mock.patch.object(_flag_selection, "telemetry_writer"),
    ):
        claim_process_selection(claimed)
        second = canonicalize_flag_selection({"tags": ["b"]})
        selection_request_url(endpoint, second, claim_process_selection(second), lambda url: url)
        report_ignored("tag_load_failed")
        warn_remote_config(claimed)

    pid = os.fork()
    if pid == 0:
        with (
            mock.patch.object(_flag_selection, "log") as log,
            mock.patch.object(_flag_selection, "telemetry_writer") as telemetry_writer,
        ):
            third = canonicalize_flag_selection({"tags": ["c"]})
            inherited = claim_process_selection(third)
            selection_request_url(endpoint, third, inherited, lambda url: url)
            report_ignored("tag_load_failed")
            warn_remote_config(claimed)
        reset = (
            inherited == claimed
            and telemetry_writer.add_count_metric.call_count == 1
            and log.error.call_count == 1
            and log.warning.call_count == 2
        )
        os._exit(0 if reset else 1)

    _, status = os.waitpid(pid, 0)
    assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0


@pytest.mark.subprocess(err=None)
def test_forked_child_counts_an_inherited_dropped_selection_once():
    from tests.openfeature.flag_selection_helpers import _child_dropped_counts

    assert _child_dropped_counts({"tags": ["t" * 201]}) == {
        "counts": [["flag_selection.dropped", {"reason": "invalid_tag"}]],
        "logged": 0,
    }


@pytest.mark.subprocess(err=None)
def test_forked_child_does_not_count_a_sent_selection():
    from tests.openfeature.flag_selection_helpers import _child_dropped_counts

    assert _child_dropped_counts({"tags": ["a"]}) == {"counts": [], "logged": 0}


def test_inherited_drop_is_counted_on_the_next_poll(poller, count_metric):
    with mock.patch.object(_flag_selection, "log"):
        _create_source({"tags": ["t" * 201]})
    count_metric.reset_mock()
    _flag_selection._reset_once_per_process_guards()

    poller([_FakeResponse(200, _ufc_body())], flag_selection_sent=False).periodic()

    assert [c.args for c in count_metric.call_args_list] == [
        (TELEMETRY_NAMESPACE.TRACERS, "flag_selection.dropped", 1, (("reason", "invalid_tag"),))
    ]


def test_released_claim_leaves_no_inherited_drop(count_metric):
    from ddtrace.internal.openfeature._flag_selection import release_process_selection

    with mock.patch.object(_flag_selection, "log"):
        _create_source({"tags": ["t" * 201]})
    release_process_selection()
    count_metric.reset_mock()
    _flag_selection._reset_once_per_process_guards()

    _flag_selection.count_inherited_drop()

    assert count_metric.call_args_list == []


def _run_provider_child_hook():
    import ddtrace.internal.openfeature._provider as provider_module

    for _ in provider_module._reset_provider_state_in_child():
        pass


def test_forked_child_gets_a_new_lifecycle_event():
    provider = _provider(None)
    inherited = provider._configuration_source_changed
    _run_provider_child_hook()
    assert provider._configuration_source_changed is not inherited
    assert not provider._configuration_source_changed.is_set()


def test_forked_child_drops_a_retired_poller_teardown_reservation():
    provider = _provider(None)
    retired = mock.Mock()
    provider._retired_configuration_sources = [retired]
    _parent_operation(provider, teardown=True, reserved=[retired])
    _run_provider_child_hook()
    assert provider._stopping_configuration_source is False
    assert provider._retired_configuration_sources == []
    retired.discard_in_child.assert_called_once_with()


def test_forked_child_drops_a_start_reservation_from_the_parent():
    provider = _provider(None)
    retired = mock.Mock()
    provider._retired_configuration_sources = [retired]
    provider._starting_configuration_source = True
    provider._configuration_source_starter = (threading.Thread(target=lambda: None), 0)
    _run_provider_child_hook()
    assert provider._starting_configuration_source is False
    # The start had not begun building its source, so it held no claim and the provider keeps its pollers.
    assert provider._retired_configuration_sources == [retired]
    retired.discard_in_child.assert_not_called()
    provider._retired_configuration_sources = []


@pytest.mark.subprocess(err=None)
def test_fork_while_a_poller_stopped_from_inside_its_poll_finishes_it_releases_the_claim():
    import threading
    import time

    import pytest

    from ddtrace.internal.openfeature import _flag_selection
    from ddtrace.internal.openfeature._flag_selection import canonicalize_flag_selection
    from ddtrace.internal.openfeature._flag_selection import claim_process_selection
    from tests.openfeature.flag_selection_helpers import _agentless_api_key
    from tests.openfeature.flag_selection_helpers import _forked_child_status
    from tests.openfeature.flag_selection_helpers import _on_recovery
    from tests.openfeature.flag_selection_helpers import _poll_without_configuration
    from tests.openfeature.flag_selection_helpers import _provider
    from tests.openfeature.flag_selection_helpers import _serving_cdn

    with pytest.MonkeyPatch.context() as monkeypatch, _serving_cdn(monkeypatch) as cdn, _agentless_api_key():
        provider = _provider({"tags": ["a"]}, timeout=0.3)
        other = canonicalize_flag_selection({"tags": ["b"]})
        _poll_without_configuration(provider, cdn, monkeypatch)
        stopped, finish = threading.Event(), threading.Event()

        def shut_down_and_hold(*args, **kwargs):
            provider.shutdown()
            stopped.set()
            finish.wait(timeout=5)

        def released():
            # The thread module restarts the stopped poller after the fork only to run its exit, which releases the
            # claim.
            deadline = time.monotonic() + 5
            while _flag_selection._claim_count and time.monotonic() < deadline:
                threading.Event().wait(0.01)
            return _flag_selection._claim_count == 0 and claim_process_selection(other) == other

        # The fork joins the poller before it forks, so the poll ends while the fork waits for it.
        ending = threading.Timer(0.3, finish.set)
        try:
            _on_recovery(provider, cdn, monkeypatch, shut_down_and_hold)
            assert stopped.wait(timeout=5)
            ending.start()
            assert _forked_child_status(released) == 0
            assert released()
        finally:
            finish.set()
            ending.cancel()
            provider.shutdown()


@pytest.mark.subprocess(err=None)
def test_child_forked_while_running_keeps_the_claim_until_its_own_shutdown():
    from openfeature.evaluation_context import EvaluationContext
    import pytest

    from ddtrace.internal.openfeature import _flag_selection
    from ddtrace.internal.openfeature._flag_selection import canonicalize_flag_selection
    from ddtrace.internal.openfeature._flag_selection import claim_process_selection
    from tests.openfeature.flag_selection_helpers import _agentless_api_key
    from tests.openfeature.flag_selection_helpers import _provider
    from tests.openfeature.flag_selection_helpers import _serving_cdn

    with pytest.MonkeyPatch.context() as monkeypatch, _serving_cdn(monkeypatch), _agentless_api_key():
        import os
        import warnings

        provider = _provider({"tags": ["a"]})
        other = canonicalize_flag_selection({"tags": ["b"]})
        try:
            provider.initialize(EvaluationContext())
            with warnings.catch_warnings():
                # CPython 3.12+ warns when a multi-threaded process forks.
                warnings.simplefilter("ignore", DeprecationWarning)
                pid = os.fork()
            if pid == 0:
                try:
                    kept = provider._configuration_source is not None and claim_process_selection(other).tags == ("a",)
                    _flag_selection.release_process_selection()
                    provider.shutdown()
                    released = provider._configuration_source is None and claim_process_selection(other) == other
                    os._exit(0 if kept and released else 1)
                except BaseException:
                    os._exit(2)

            _, status = os.waitpid(pid, 0)
            assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0
            assert claim_process_selection(other).tags == ("a",)
            _flag_selection.release_process_selection()
        finally:
            provider.shutdown()


def _parent_operation(provider, teardown, source=None, reserved=(), started=False):
    import ddtrace.internal.openfeature._provider as provider_module

    operation = provider_module._LifecycleOperation(teardown)
    # A thread of the parent, which does not survive the fork.
    operation.owner = threading.Thread(target=lambda: None)
    operation.source = source
    operation.started = started
    operation.shutdowns = provider._configuration_source_shutdowns
    operation.reserved = list(reserved)
    if reserved:
        provider._stopping_configuration_source = True
        provider._configuration_source_stopper = operation.owner
    provider._configuration_source_operations.append(operation)
    return operation


def test_forked_child_recounts_without_the_inherited_selection_lock():
    provider = _provider(None)
    provider._configuration_source = mock.Mock()
    _parent_operation(provider, teardown=True, reserved=[provider._configuration_source])
    claim_process_selection(canonicalize_flag_selection({"tags": ["a"]}))
    # A parent thread held the selection lock at the fork; the recount must not wait for it.
    _flag_selection._lock.acquire()
    try:
        hook = threading.Thread(target=_run_provider_child_hook)
        hook.start()
        hook.join(timeout=2)
        assert not hook.is_alive()
    finally:
        _flag_selection._lock.release()
    assert _flag_selection._claim_count == 0


def test_forked_child_discards_each_overlapping_start_on_its_own():
    provider = _provider(None)
    cancelled, replacement = mock.Mock(), mock.Mock()
    _parent_operation(provider, teardown=False, source=cancelled)
    _parent_operation(provider, teardown=False, source=replacement)
    _run_provider_child_hook()
    cancelled.discard_in_child.assert_called_once_with()
    replacement.discard_in_child.assert_called_once_with()
    assert provider._configuration_source_operations == []


def test_forked_child_recounts_once_the_forking_threads_operation_ends():
    import ddtrace.internal.openfeature._provider as provider_module

    interrupted = _provider(None)
    interrupted._configuration_source = mock.Mock()
    _parent_operation(interrupted, teardown=True, reserved=[interrupted._configuration_source])
    forking = _provider(None)
    for _ in range(2):
        claim_process_selection(canonicalize_flag_selection({"tags": ["a"]}))
    with provider_module._lifecycle_operation(forking, teardown=False):
        _run_provider_child_hook()
        # The forking thread's start carries on in the child, so its claim is not known yet.
        assert _flag_selection._claim_count == 2
    assert _flag_selection._claim_count == 0


@pytest.mark.parametrize("published", [False, True], ids=["started", "published"])
def test_forked_child_keeps_a_start_whose_poller_was_running(published):
    provider = _provider(None)
    source = mock.Mock()
    if published:
        provider._configuration_source = source
    _parent_operation(provider, teardown=False, source=source, started=True)
    _run_provider_child_hook()
    assert provider._configuration_source is source
    source.discard_in_child.assert_not_called()
    assert _flag_selection._claim_count == 1
    provider._configuration_source = None
    _flag_selection.release_process_selection()


def test_forked_child_leaves_pollers_a_teardown_had_not_reserved():
    provider = _provider(None)
    current = mock.Mock()
    provider._configuration_source = current
    provider._stopping_configuration_source = True
    provider._configuration_source_stopper = threading.current_thread()
    # A second shutdown that had not reserved anything, beside the forking thread's own teardown.
    _parent_operation(provider, teardown=True)
    _run_provider_child_hook()
    assert provider._configuration_source is current
    current.discard_in_child.assert_not_called()
    assert provider._stopping_configuration_source is True
    provider._configuration_source = None
    provider._stopping_configuration_source = False
    provider._configuration_source_stopper = None
    _flag_selection.release_process_selection()


@pytest.mark.subprocess(
    err=None,
    parametrize={"POLLER_STARTED": ["claimed", "poller-started"], "WAIT_EXPIRES": ["finishes", "wait-expires"]},
)
def test_fork_during_a_start_leaves_the_child_a_correct_claim():
    import os
    import threading

    import pytest

    from ddtrace.internal.openfeature import _flag_selection
    from tests.openfeature.flag_selection_helpers import _agentless_api_key
    from tests.openfeature.flag_selection_helpers import _child_selection_released
    from tests.openfeature.flag_selection_helpers import _fork_during
    from tests.openfeature.flag_selection_helpers import _initialize_quietly
    from tests.openfeature.flag_selection_helpers import _poller_runs
    from tests.openfeature.flag_selection_helpers import _provider
    from tests.openfeature.flag_selection_helpers import _serving_cdn

    wait_expires = os.environ["WAIT_EXPIRES"] == "wait-expires"
    poller_started = os.environ["POLLER_STARTED"] == "poller-started"

    with pytest.MonkeyPatch.context() as monkeypatch, _serving_cdn(monkeypatch), _agentless_api_key():
        import ddtrace.internal.openfeature._source_selection as source_selection

        provider = _provider({"tags": ["a"]})
        create = source_selection.create_agentless_source
        holding = threading.Event()
        resume = threading.Event()
        created = []

        def create_then_hold(*args, **kwargs):
            source = create(*args, **kwargs)
            created.append(source)
            if not poller_started:
                holding.set()
                resume.wait(timeout=5)
                return source
            start = source.start

            def start_then_hold():
                start()
                holding.set()
                resume.wait(timeout=5)

            source.start = start_then_hold
            return source

        monkeypatch.setattr(source_selection, "create_agentless_source", create_then_hold)
        starting = threading.Thread(target=lambda: _initialize_quietly(provider))
        starting.start()

        def check():
            if wait_expires:
                # The start was still inside source.start() or before it: none of its pollers runs and its claim is
                # gone.
                unowned = poller_started and _poller_runs(created[0], 0.4)
                reset = provider._configuration_source is None and not provider._configuration_source_operations
                return reset and not unowned and _child_selection_released(provider)
            stored = provider._configuration_source is created[0] and _flag_selection._claim_count == 1
            running = not poller_started or _poller_runs(created[0], 2.0)
            return stored and running and _child_selection_released(provider)

        try:
            assert holding.wait(timeout=5)
            assert _fork_during(resume, monkeypatch, wait_expires, check) == 0
        finally:
            resume.set()
            starting.join(timeout=5)
            provider.shutdown()


@pytest.mark.subprocess(err=None, parametrize={"WAIT_EXPIRES": ["finishes", "wait-expires"]})
def test_fork_during_a_teardown_leaves_the_child_a_correct_claim():
    import os
    import threading

    from openfeature.evaluation_context import EvaluationContext
    import pytest

    from tests.openfeature.flag_selection_helpers import _agentless_api_key
    from tests.openfeature.flag_selection_helpers import _child_selection_released
    from tests.openfeature.flag_selection_helpers import _fork_during
    from tests.openfeature.flag_selection_helpers import _poller_runs
    from tests.openfeature.flag_selection_helpers import _provider
    from tests.openfeature.flag_selection_helpers import _serving_cdn

    wait_expires = os.environ["WAIT_EXPIRES"] == "wait-expires"

    with pytest.MonkeyPatch.context() as monkeypatch, _serving_cdn(monkeypatch), _agentless_api_key():
        provider = _provider({"tags": ["a"]})
        joined = threading.Event()
        resume = threading.Event()
        stopping = None
        try:
            provider.initialize(EvaluationContext())
            source = provider._configuration_source
            join = source.join

            def join_then_hold(*args, **kwargs):
                join(*args, **kwargs)
                joined.set()
                resume.wait(timeout=5)

            monkeypatch.setattr(source, "join", join_then_hold)
            stopping = threading.Thread(target=provider.shutdown)
            stopping.start()
            assert joined.wait(timeout=5)

            def check():
                done = provider._configuration_source is None and not provider._stopping_configuration_source
                return done and not _poller_runs(source, 0.4) and _child_selection_released(provider)

            assert _fork_during(resume, monkeypatch, wait_expires, check) == 0
        finally:
            resume.set()
            if stopping is not None:
                stopping.join(timeout=5)
            provider.shutdown()


@pytest.mark.subprocess(err=None, parametrize={"WAIT_EXPIRES": ["finishes", "wait-expires"]})
def test_fork_during_a_cancelled_start_leaves_the_child_a_correct_claim():
    import os
    import threading

    import pytest

    from tests.openfeature.flag_selection_helpers import _agentless_api_key
    from tests.openfeature.flag_selection_helpers import _child_selection_released
    from tests.openfeature.flag_selection_helpers import _fork_during
    from tests.openfeature.flag_selection_helpers import _initialize_quietly
    from tests.openfeature.flag_selection_helpers import _poller_runs
    from tests.openfeature.flag_selection_helpers import _provider
    from tests.openfeature.flag_selection_helpers import _serving_cdn

    wait_expires = os.environ["WAIT_EXPIRES"] == "wait-expires"

    with pytest.MonkeyPatch.context() as monkeypatch, _serving_cdn(monkeypatch), _agentless_api_key():
        import ddtrace.internal.openfeature._source_selection as source_selection

        provider = _provider({"tags": ["a"]})
        create = source_selection.create_agentless_source
        started = threading.Event()
        resume_start = threading.Event()
        joined = threading.Event()
        resume = threading.Event()
        created = []

        def create_then_hold(*args, **kwargs):
            source = create(*args, **kwargs)
            created.append(source)
            start, join = source.start, source.join

            def start_then_hold():
                start()
                started.set()
                resume_start.wait(timeout=5)

            def join_then_hold(*args, **kwargs):
                join(*args, **kwargs)
                joined.set()
                resume.wait(timeout=5)

            source.start = start_then_hold
            source.join = join_then_hold
            return source

        monkeypatch.setattr(source_selection, "create_agentless_source", create_then_hold)
        starting = threading.Thread(target=lambda: _initialize_quietly(provider))
        starting.start()
        try:
            assert started.wait(timeout=5)
            # The shutdown cancels the start, which then joins its poller and releases its claim itself.
            provider.shutdown()
            resume_start.set()
            assert joined.wait(timeout=5)

            def check():
                gone = provider._configuration_source is None and not provider._configuration_source_operations
                return gone and not _poller_runs(created[0], 0.4) and _child_selection_released(provider)

            assert _fork_during(resume, monkeypatch, wait_expires, check) == 0
        finally:
            resume_start.set()
            resume.set()
            starting.join(timeout=5)
            provider.shutdown()
