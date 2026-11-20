"""Flag selection across the provider lifecycle: claims, starts, shutdowns and their races."""

import json
import logging
import threading
import time
from unittest import mock

from openfeature.evaluation_context import EvaluationContext
from openfeature.exception import ProviderNotReadyError
from openfeature.provider import ProviderStatus
import pytest

from ddtrace.internal.openfeature import _flag_selection
from ddtrace.internal.openfeature._agentless_source import AgentlessConfigurationSource
from ddtrace.internal.openfeature._flag_selection import canonicalize_flag_selection
from ddtrace.internal.openfeature._flag_selection import claim_process_selection
import ddtrace.internal.openfeature._provider as provider_module
from tests.openfeature.flag_selection_helpers import SECOND_SELECTION_WARNING
from tests.openfeature.flag_selection_helpers import SELECTION_LOGGER
from tests.openfeature.flag_selection_helpers import _FakeResponse
from tests.openfeature.flag_selection_helpers import _flag_body
from tests.openfeature.flag_selection_helpers import _initialize_quietly
from tests.openfeature.flag_selection_helpers import _on_recovery
from tests.openfeature.flag_selection_helpers import _poll_without_configuration
from tests.openfeature.flag_selection_helpers import _provider
from tests.openfeature.flag_selection_helpers import _sent_selection
from tests.utils import override_global_config


def test_providers_share_the_first_selection_until_the_last_shutdown(cdn, api_key, caplog):
    first = _provider({"tags": ["a"]})
    second = _provider({"tags": ["b"]})
    third = _provider({"tags": ["c"]})
    later = _provider({"tags": ["c"]})
    try:
        with caplog.at_level(logging.WARNING, logger=SELECTION_LOGGER):
            first.initialize(EvaluationContext())
            second.initialize(EvaluationContext())
            first.shutdown()
            third.initialize(EvaluationContext())
            assert _sent_selection(second._configuration_source) == (["a"], [])
            assert _sent_selection(third._configuration_source) == (["a"], [])
            assert caplog.messages.count(SECOND_SELECTION_WARNING) == 1

            second.shutdown()
            third.shutdown()
            later.initialize(EvaluationContext())
            assert _sent_selection(later._configuration_source) == (["c"], [])
    finally:
        for provider in (first, second, third, later):
            provider.shutdown()


def test_claim_is_released_when_the_source_cannot_start(cdn, api_key, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(AgentlessConfigurationSource, "start", fail)
    provider = _provider({"tags": ["a"]})
    try:
        with pytest.raises(RuntimeError):
            provider.initialize(EvaluationContext())

        other = canonicalize_flag_selection({"tags": ["b"]})
        assert claim_process_selection(other) == other
    finally:
        provider.shutdown()


def test_repeated_shutdown_does_not_release_another_providers_claim(cdn, api_key):
    first = _provider({"tags": ["a"]})
    second = _provider({"tags": ["b"]})
    later = _provider({"tags": ["c"]})
    try:
        first.initialize(EvaluationContext())
        second.initialize(EvaluationContext())
        first.shutdown()
        first.shutdown()
        later.initialize(EvaluationContext())
        assert _sent_selection(later._configuration_source) == (["a"], [])
    finally:
        for provider in (first, second, later):
            provider.shutdown()


def test_concurrent_shutdown_releases_the_claim_once(cdn, api_key, monkeypatch):
    first = _provider({"tags": ["a"]})
    second = _provider({"tags": ["b"]})
    later = _provider({"tags": ["c"]})
    joining = threading.Event()
    resume = threading.Event()
    try:
        first.initialize(EvaluationContext())
        second.initialize(EvaluationContext())
        source = first._configuration_source
        join = source.join

        def blocking_join(*args, **kwargs):
            joining.set()
            resume.wait(timeout=5)
            return join(*args, **kwargs)

        monkeypatch.setattr(source, "join", blocking_join)
        stopping = threading.Thread(target=first.shutdown)
        stopping.start()
        assert joining.wait(timeout=5)
        # The poller is stopped, so a second shutdown racing the join would see ServiceStatusError.
        first.shutdown()
        resume.set()
        stopping.join(timeout=5)

        later.initialize(EvaluationContext())
        assert _sent_selection(later._configuration_source) == (["a"], [])
    finally:
        resume.set()
        for provider in (first, second, later):
            provider.shutdown()


def test_log_handler_shutting_the_provider_down_during_start_does_not_deadlock(cdn, caplog):
    provider = _provider({"tags": ["a"]}, timeout=0.2)
    found = []

    class _ShutsDownOnError(logging.Handler):
        def emit(self, record):
            if record.levelno >= logging.ERROR and not found:
                provider.shutdown()
                found.append(provider._configuration_source)

    # The provider logs the missing key while the start is reserved.
    source_logger = logging.getLogger("ddtrace.internal.openfeature._provider")
    handler = _ShutsDownOnError()
    source_logger.addHandler(handler)

    def initialize():
        try:
            provider.initialize(EvaluationContext())
        except ProviderNotReadyError:
            pass

    # A daemon thread, so a deadlocked start fails the test instead of hanging the run.
    starting = threading.Thread(target=initialize, daemon=True)
    try:
        with override_global_config({"_dd_api_key": None, "_dd_site": "datadoghq.com", "env": None}):
            # DEBUG turns the per-call-site rate limiter off, so the missing-key error reaches the handler.
            with caplog.at_level(logging.DEBUG, logger=source_logger.name):
                starting.start()
                starting.join(timeout=5)
        assert not starting.is_alive()
        assert found == [None]
        assert provider._configuration_source is None
    finally:
        source_logger.removeHandler(handler)
        if not starting.is_alive():
            provider.shutdown()


class _OnFirstNotSentError(logging.Handler):
    """Run ``action`` from the first "not sent" error, which a start logs while it builds the source."""

    def __init__(self, action):
        super().__init__()
        self.action = action
        self.ran = False

    def emit(self, record):
        if not self.ran and record.getMessage().startswith("flag_selection was not sent"):
            self.ran = True
            self.action()


@pytest.fixture
def created_sources(monkeypatch):
    import ddtrace.internal.openfeature._source_selection as source_selection

    create = source_selection.create_agentless_source
    created = []

    def recording_create(*args, **kwargs):
        source = create(*args, **kwargs)
        created.append(source)
        return source

    monkeypatch.setattr(source_selection, "create_agentless_source", recording_create)
    return created


def test_shutdown_from_a_log_handler_cancels_the_start(cdn, api_key, created_sources):
    provider = _provider({"tags": "malformed"})
    later = _provider({"tags": ["b"]})
    handler = _OnFirstNotSentError(provider.shutdown)
    selection_logger = logging.getLogger(SELECTION_LOGGER)
    selection_logger.addHandler(handler)
    try:
        with pytest.raises(ProviderNotReadyError):
            provider.initialize(EvaluationContext())
        assert handler.ran
        [source] = created_sources
        # The cancelled start released its claim and never started the poller.
        assert provider._configuration_source is None
        assert source._worker is None
        later.initialize(EvaluationContext())
        assert _sent_selection(later._configuration_source) == (["b"], [])
    finally:
        selection_logger.removeHandler(handler)
        provider.shutdown()
        later.shutdown()


def test_initialize_from_a_log_handler_during_the_start_starts_one_poller(cdn, api_key, created_sources):
    provider = _provider({"tags": "malformed"}, timeout=0.3)
    later = _provider({"tags": ["b"]})

    def initialize_again():
        try:
            provider.initialize(EvaluationContext())
        except ProviderNotReadyError:
            pass

    handler = _OnFirstNotSentError(initialize_again)
    selection_logger = logging.getLogger(SELECTION_LOGGER)
    selection_logger.addHandler(handler)
    try:
        provider.initialize(EvaluationContext())
        assert handler.ran
        assert len(created_sources) == 1
        assert provider._configuration_source is created_sources[0]

        provider.shutdown()
        later.initialize(EvaluationContext())
        assert _sent_selection(later._configuration_source) == (["b"], [])
    finally:
        selection_logger.removeHandler(handler)
        provider.shutdown()
        later.shutdown()


def test_shutdown_from_another_thread_cancels_a_start_in_progress(cdn, api_key, monkeypatch):
    import ddtrace.internal.openfeature._source_selection as source_selection

    provider = _provider({"tags": ["a"]})
    later = _provider({"tags": ["b"]})
    create = source_selection.create_agentless_source
    creating = threading.Event()
    resume = threading.Event()

    def held_create(*args, **kwargs):
        source = create(*args, **kwargs)
        creating.set()
        resume.wait(timeout=5)
        return source

    monkeypatch.setattr(source_selection, "create_agentless_source", held_create)
    outcome = []

    def initialize():
        try:
            provider.initialize(EvaluationContext())
            outcome.append("ready")
        except ProviderNotReadyError:
            outcome.append("cancelled")

    starting = threading.Thread(target=initialize)
    starting.start()
    try:
        assert creating.wait(timeout=5)
        provider.shutdown()
        resume.set()
        starting.join(timeout=5)
        assert outcome == ["cancelled"]
        assert provider._configuration_source is None
        later.initialize(EvaluationContext())
        assert _sent_selection(later._configuration_source) == (["b"], [])
    finally:
        resume.set()
        starting.join(timeout=5)
        provider.shutdown()
        later.shutdown()


class _CheckedEvent(threading.Event):
    """Sets waiting once a start that waits for another one checks it: the wait polls is_set() between sleeps."""

    def __init__(self):
        super().__init__()
        self.waiting = threading.Event()

    def is_set(self):
        self.waiting.set()
        return super().is_set()


def _hold_source_creation(monkeypatch, resume):
    """Make each start wait for ``resume`` before it builds its source, and return an event set when one waits."""
    import ddtrace.internal.openfeature._source_selection as source_selection

    create = source_selection.create_agentless_source
    holding = threading.Event()

    def held_create(*args, **kwargs):
        holding.set()
        resume.wait(timeout=5)
        return create(*args, **kwargs)

    monkeypatch.setattr(source_selection, "create_agentless_source", held_create)
    return holding


def test_concurrent_initialize_reports_ready_only_on_the_new_selection(cdn, api_key, monkeypatch):
    cdn.respond = lambda target: _FakeResponse(200, _flag_body("selected-a"))
    first = _provider({"tags": ["a"]})
    first.initialize(EvaluationContext())
    first.shutdown()

    cdn.respond = lambda target: _FakeResponse(200, _flag_body("selected-b"))
    provider = _provider({"tags": ["b"]})
    resume = threading.Event()
    holding = _hold_source_creation(monkeypatch, resume)
    starting = threading.Thread(target=provider.initialize, args=(EvaluationContext(),))
    starting.start()
    try:
        assert holding.wait(timeout=5)
        # The second initialization must wait for the first to claim B, not pass on A's snapshot.
        threading.Timer(0.2, resume.set).start()
        provider.initialize(EvaluationContext())
        assert provider._status == ProviderStatus.READY
        assert provider.resolve_boolean_details("selected-b", False).value is True
    finally:
        resume.set()
        starting.join(timeout=5)
        provider.shutdown()


def test_initialize_after_a_cancelled_start_ends_with_a_poller(cdn, api_key, monkeypatch):
    provider = _provider({"tags": ["a"]}, timeout=1.0)
    later = _provider({"tags": ["b"]})
    resume = threading.Event()
    holding = _hold_source_creation(monkeypatch, resume)
    outcomes = {}

    def initialize(name):
        try:
            provider.initialize(EvaluationContext())
            outcomes[name] = provider._status
        except ProviderNotReadyError:
            outcomes[name] = "cancelled"

    cancelled = threading.Thread(target=initialize, args=("cancelled",))
    cancelled.start()
    reinitializing = None
    try:
        assert holding.wait(timeout=5)
        provider.shutdown()
        # Nothing replaces this event until the held start ends, so it is the one the re-initialization checks.
        signal = provider._configuration_source_changed = _CheckedEvent()
        reinitializing = threading.Thread(target=initialize, args=("reinitialized",))
        reinitializing.start()
        assert signal.waiting.wait(timeout=5)
        resume.set()
        cancelled.join(timeout=5)
        reinitializing.join(timeout=5)
        assert outcomes == {"cancelled": "cancelled", "reinitialized": ProviderStatus.READY}
        assert provider._configuration_source is not None

        provider.shutdown()
        later.initialize(EvaluationContext())
        assert _sent_selection(later._configuration_source) == (["b"], [])
    finally:
        resume.set()
        cancelled.join(timeout=5)
        if reinitializing is not None:
            reinitializing.join(timeout=5)
        provider.shutdown()
        later.shutdown()


def test_failed_teardown_of_a_cancelled_start_keeps_the_claim_for_the_next_shutdown(cdn, api_key, monkeypatch):
    import ddtrace.internal.openfeature._source_selection as source_selection

    provider = _provider({"tags": ["a"]})
    later = _provider({"tags": ["b"]})
    create = source_selection.create_agentless_source
    started = threading.Event()
    resume = threading.Event()
    sources = []

    def create_with_a_held_start(*args, **kwargs):
        source = create(*args, **kwargs)
        if sources:
            return source
        sources.append(source)
        start, join = source.start, source.join
        failures = [RuntimeError("join failed")]

        def held_start():
            start()
            started.set()
            resume.wait(timeout=5)

        def join_failing_once(*join_args, **join_kwargs):
            if failures:
                raise failures.pop()
            return join(*join_args, **join_kwargs)

        source.start = held_start
        source.join = join_failing_once
        return source

    monkeypatch.setattr(source_selection, "create_agentless_source", create_with_a_held_start)
    errors = []

    def initialize():
        try:
            provider.initialize(EvaluationContext())
        except RuntimeError as error:
            errors.append(error)

    starting = threading.Thread(target=initialize)
    starting.start()
    try:
        assert started.wait(timeout=5)
        provider.shutdown()
        resume.set()
        starting.join(timeout=5)
        assert len(errors) == 1
        [source] = sources
        assert provider._configuration_source is None
        assert provider._retired_configuration_sources == [source]
        other = canonicalize_flag_selection({"tags": ["b"]})
        assert claim_process_selection(other).tags == ("a",)
        _flag_selection.release_process_selection()

        provider.shutdown()
        assert provider._configuration_source is None
        later.initialize(EvaluationContext())
        assert _sent_selection(later._configuration_source) == (["b"], [])
    finally:
        resume.set()
        starting.join(timeout=5)
        provider.shutdown()
        later.shutdown()


def test_initialize_after_a_failed_teardown_starts_a_new_poller(cdn, api_key, monkeypatch):
    provider = _provider({"tags": ["a"]})
    later = _provider({"tags": ["b"]})
    try:
        provider.initialize(EvaluationContext())
        retired = provider._configuration_source
        monkeypatch.setattr(retired, "join", mock.Mock(side_effect=[RuntimeError("join failed"), None]))
        with pytest.raises(RuntimeError):
            provider.shutdown()

        # The retired poller does not count as started: initialization starts and stores a new one.
        provider.initialize(EvaluationContext())
        assert provider._configuration_source is not None
        assert provider._configuration_source is not retired
        assert provider._retired_configuration_sources == [retired]

        provider.shutdown()
        assert provider._configuration_source is None
        assert provider._retired_configuration_sources == []
        later.initialize(EvaluationContext())
        assert _sent_selection(later._configuration_source) == (["b"], [])
    finally:
        provider.shutdown()
        later.shutdown()


def test_failed_teardown_of_a_cancelled_start_keeps_its_claim_beside_a_replacement(cdn, api_key, monkeypatch):
    import ddtrace.internal.openfeature._source_selection as source_selection

    provider = _provider({"tags": ["a"]})
    later = _provider({"tags": ["b"]})
    create = source_selection.create_agentless_source
    started = threading.Event()
    resume = threading.Event()
    sources = []

    def create_with_a_held_start(*args, **kwargs):
        source = create(*args, **kwargs)
        sources.append(source)
        if len(sources) > 1:
            return source
        start = source.start

        def held_start():
            start()
            started.set()
            resume.wait(timeout=5)

        def join_after_the_replacement_is_stored(*join_args, **join_kwargs):
            for _ in range(500):
                if provider._configuration_source is not None:
                    break
                threading.Event().wait(0.01)
            raise RuntimeError("join failed")

        source.start = held_start
        source.join = join_after_the_replacement_is_stored
        return source

    monkeypatch.setattr(source_selection, "create_agentless_source", create_with_a_held_start)
    outcomes = {}

    def initialize(name):
        try:
            provider.initialize(EvaluationContext())
            outcomes[name] = provider._status
        except (ProviderNotReadyError, RuntimeError) as error:
            outcomes[name] = type(error).__name__

    cancelled = threading.Thread(target=initialize, args=("cancelled",))
    cancelled.start()
    replacement = None
    try:
        assert started.wait(timeout=5)
        provider.shutdown()
        # Nothing replaces this event until the held start ends, so it is the one the replacement checks.
        signal = provider._configuration_source_changed = _CheckedEvent()
        replacement = threading.Thread(target=initialize, args=("replacement",))
        replacement.start()
        assert signal.waiting.wait(timeout=5)
        resume.set()
        cancelled.join(timeout=5)
        replacement.join(timeout=5)
        assert outcomes == {"cancelled": "RuntimeError", "replacement": ProviderStatus.READY}
        first, second = sources
        assert provider._configuration_source is second
        assert provider._retired_configuration_sources == [first]

        monkeypatch.setattr(first, "join", lambda *args, **kwargs: None)
        provider.shutdown()
        assert provider._retired_configuration_sources == []
        later.initialize(EvaluationContext())
        assert _sent_selection(later._configuration_source) == (["b"], [])
    finally:
        resume.set()
        cancelled.join(timeout=5)
        if replacement is not None:
            replacement.join(timeout=5)
        for source in sources:
            monkeypatch.setattr(source, "join", lambda *args, **kwargs: None)
        provider.shutdown()
        later.shutdown()


def test_shutdown_then_initialize_from_a_log_handler_rejects_the_nested_initialize(cdn, api_key, created_sources):
    # A matching snapshot: no selection and the dropped selection below both load every flag.
    first = _provider(None)
    first.initialize(EvaluationContext())
    first.shutdown()
    provider = _provider({"tags": "malformed"}, timeout=0.3)
    later = _provider({"tags": ["b"]})
    nested = []

    def shut_down_and_initialize_again():
        provider.shutdown()
        try:
            provider.initialize(EvaluationContext())
            nested.append(provider._status)
        except ProviderNotReadyError:
            nested.append("rejected")

    handler = _OnFirstNotSentError(shut_down_and_initialize_again)
    selection_logger = logging.getLogger(SELECTION_LOGGER)
    selection_logger.addHandler(handler)
    try:
        with pytest.raises(ProviderNotReadyError):
            provider.initialize(EvaluationContext())
        assert nested == ["rejected"]
        assert provider._configuration_source is None
        later.initialize(EvaluationContext())
        assert _sent_selection(later._configuration_source) == (["b"], [])
    finally:
        selection_logger.removeHandler(handler)
        provider.shutdown()
        later.shutdown()


def test_shutdown_wakes_a_start_waiting_for_an_in_flight_start(cdn, api_key, monkeypatch):
    provider = _provider({"tags": ["a"]})
    resume = threading.Event()
    holding = _hold_source_creation(monkeypatch, resume)
    outcomes = {}

    def initialize(name):
        try:
            provider.initialize(EvaluationContext())
            outcomes[name] = provider._status
        except ProviderNotReadyError:
            outcomes[name] = "cancelled"

    first = threading.Thread(target=initialize, args=("first",))
    first.start()
    waiting = None
    try:
        assert holding.wait(timeout=5)
        # The held start does not replace this event, so it is the one the waiting start checks.
        signal = provider._configuration_source_changed = _CheckedEvent()
        waiting = threading.Thread(target=initialize, args=("waiting",))
        waiting.start()
        assert signal.waiting.wait(timeout=5)
        provider.shutdown()
        # The waiting start began before the shutdown, so it ends without waiting for the held start.
        waiting.join(timeout=2)
        assert not waiting.is_alive()
        assert outcomes == {"waiting": "cancelled"}
    finally:
        resume.set()
        first.join(timeout=5)
        if waiting is not None:
            waiting.join(timeout=5)
        provider.shutdown()


def test_waiting_start_is_bounded_by_the_initialization_timeout(cdn, api_key):
    provider = _provider({"tags": "malformed"}, timeout=0.3)
    delegated = []

    def initialize_on_another_thread_and_wait():
        def initialize():
            try:
                provider.initialize(EvaluationContext())
                delegated.append(provider._status)
            except ProviderNotReadyError:
                delegated.append("timed out")

        thread = threading.Thread(target=initialize)
        thread.start()
        thread.join(timeout=5)

    handler = _OnFirstNotSentError(initialize_on_another_thread_and_wait)
    selection_logger = logging.getLogger(SELECTION_LOGGER)
    selection_logger.addHandler(handler)
    try:
        started_at = time.monotonic()
        try:
            provider.initialize(EvaluationContext())
        except ProviderNotReadyError:
            # Waiting for the delegated initialization can use up this initialization's own timeout.
            pass
        # The delegated initialization waits for the start that waits for it, but only until its deadline.
        assert time.monotonic() - started_at < 3
        assert delegated == ["timed out"]
    finally:
        selection_logger.removeHandler(handler)
        provider.shutdown()


def test_initialize_that_times_out_waiting_for_a_start_does_not_report_ready(cdn, api_key, monkeypatch):
    cdn.respond = lambda target: _FakeResponse(200, _flag_body("selected-a"))
    first = _provider({"tags": ["a"]})
    first.initialize(EvaluationContext())
    first.shutdown()

    cdn.respond = lambda target: _FakeResponse(200, _flag_body("selected-b"))
    provider = _provider({"tags": ["b"]}, timeout=0.3)
    resume = threading.Event()
    holding = _hold_source_creation(monkeypatch, resume)
    starting = threading.Thread(target=lambda: _initialize_quietly(provider))
    starting.start()
    try:
        assert holding.wait(timeout=5)
        # The first start has not claimed B yet, so A's snapshot must not make this one READY.
        with pytest.raises(ProviderNotReadyError):
            provider.initialize(EvaluationContext())
    finally:
        resume.set()
        starting.join(timeout=5)
        provider.shutdown()


def test_start_during_a_retired_poller_teardown_waits_for_it(cdn, api_key, monkeypatch):
    provider = _provider({"tags": ["a"]})
    later = _provider({"tags": ["b"]})
    joining = threading.Event()
    resume = threading.Event()
    try:
        provider.initialize(EvaluationContext())
        retired = provider._configuration_source
        joins = [RuntimeError("join failed")]

        def join_failing_then_held(*args, **kwargs):
            if joins:
                raise joins.pop()
            joining.set()
            resume.wait(timeout=5)

        monkeypatch.setattr(retired, "join", join_failing_then_held)
        with pytest.raises(RuntimeError):
            provider.shutdown()
        assert provider._retired_configuration_sources == [retired]

        stopping = threading.Thread(target=provider.shutdown)
        stopping.start()
        assert joining.wait(timeout=5)
        # The teardown in its join does not replace this event, so it is the one the start checks.
        signal = provider._configuration_source_changed = _CheckedEvent()
        reinitializing = threading.Thread(target=lambda: _initialize_quietly(provider))
        reinitializing.start()
        assert signal.waiting.wait(timeout=5)
        # The teardown owns no current poller, and a start must not add one it does not own.
        assert provider._configuration_source is None
        resume.set()
        stopping.join(timeout=5)
        reinitializing.join(timeout=5)
        assert provider._retired_configuration_sources == []
        assert provider._configuration_source is not None

        provider.shutdown()
        later.initialize(EvaluationContext())
        assert _sent_selection(later._configuration_source) == (["b"], [])
    finally:
        resume.set()
        provider.shutdown()
        later.shutdown()


def test_initialize_that_times_out_ignores_a_stale_readiness_signal(cdn, api_key, monkeypatch):
    provider = _provider({"tags": ["a"]}, timeout=0.3)
    joining = threading.Event()
    resume = threading.Event()
    stopping = None
    try:
        provider.initialize(EvaluationContext())
        retired = provider._configuration_source
        joins = [RuntimeError("join failed")]

        def join_failing_then_held(*args, **kwargs):
            if joins:
                raise joins.pop()
            joining.set()
            resume.wait(timeout=5)

        monkeypatch.setattr(retired, "join", join_failing_then_held)
        # The failed shutdown raises, but it has already dropped the first initialization's readiness signal.
        with pytest.raises(RuntimeError):
            provider.shutdown()
        assert not provider._config_received.is_set()

        stopping = threading.Thread(target=provider.shutdown)
        stopping.start()
        assert joining.wait(timeout=5)
        with pytest.raises(ProviderNotReadyError):
            provider.initialize(EvaluationContext())
    finally:
        resume.set()
        if stopping is not None:
            stopping.join(timeout=5)
        provider.shutdown()


def test_initialize_that_times_out_fails_even_after_the_in_flight_start_delivered(cdn, api_key, monkeypatch):
    import ddtrace.internal.openfeature._source_selection as source_selection

    provider = _provider({"tags": ["a"]}, timeout=0.3)
    create = source_selection.create_agentless_source
    delivered = threading.Event()
    resume = threading.Event()

    def create_with_a_held_store(*args, **kwargs):
        source = create(*args, **kwargs)
        start = source.start

        def start_then_hold():
            start()
            # The poller delivers the configuration before the starting thread stores it.
            for _ in range(500):
                if provider._config_received.is_set():
                    break
                threading.Event().wait(0.01)
            delivered.set()
            resume.wait(timeout=5)

        source.start = start_then_hold
        return source

    monkeypatch.setattr(source_selection, "create_agentless_source", create_with_a_held_store)
    outcome = []

    def initialize_first():
        try:
            provider.initialize(EvaluationContext())
            outcome.append(provider._status)
        except ProviderNotReadyError:
            outcome.append("timed out")

    starting = threading.Thread(target=initialize_first)
    starting.start()
    try:
        assert delivered.wait(timeout=5)
        # The in-flight start has not finished, so this initialization times out, signal or not.
        with pytest.raises(ProviderNotReadyError):
            provider.initialize(EvaluationContext())
        resume.set()
        starting.join(timeout=5)
        # The start that did deliver reports READY through its own initialization.
        assert outcome == [ProviderStatus.READY]
    finally:
        resume.set()
        starting.join(timeout=5)
        provider.shutdown()


@pytest.mark.parametrize("first_still_running", [False, True], ids=["first-shut-down", "first-running"])
def test_initialize_that_times_out_ignores_a_notification_from_another_selection(
    cdn, api_key, monkeypatch, first_still_running
):
    from ddtrace.internal.openfeature._config import _register_provider

    cdn.respond = lambda target: _FakeResponse(200, _flag_body("selected-a"))
    first = _provider({"tags": ["a"]})
    first.initialize(EvaluationContext())
    provider = _provider({"tags": ["b"]}, timeout=0.3)
    # Selection A's poller notifies the B provider once it is registered; A may still hold its claim.
    _register_provider(provider)
    first._configuration_source._apply_configuration(json.loads(_flag_body("selected-a"))["data"]["attributes"])
    assert provider._config_received.is_set()
    if not first_still_running:
        first.shutdown()

    resume = threading.Event()
    holding = _hold_source_creation(monkeypatch, resume)
    starting = threading.Thread(target=lambda: _initialize_quietly(provider))
    starting.start()
    try:
        assert holding.wait(timeout=5)
        with pytest.raises(ProviderNotReadyError):
            provider.initialize(EvaluationContext())
    finally:
        resume.set()
        starting.join(timeout=5)
        provider.shutdown()
        first.shutdown()


def test_concurrent_starts_start_one_poller(cdn, api_key, monkeypatch):
    import ddtrace.internal.openfeature._source_selection as source_selection

    provider = _provider({"tags": ["a"]})
    later = _provider({"tags": ["b"]})
    create = source_selection.create_agentless_source
    creating = threading.Event()
    created = []

    def slow_create(*args, **kwargs):
        source = create(*args, **kwargs)
        created.append(source)
        creating.set()
        # Leave a concurrent start time to pass an unguarded check before this source is stored.
        threading.Event().wait(0.2)
        return source

    monkeypatch.setattr(source_selection, "create_agentless_source", slow_create)
    first = threading.Thread(target=provider.initialize, args=(EvaluationContext(),))
    first.start()
    try:
        assert creating.wait(timeout=5)
        provider.initialize(EvaluationContext())
        first.join(timeout=5)
        assert len(created) == 1
        assert provider._configuration_source is created[0]

        provider.shutdown()
        later.initialize(EvaluationContext())
        assert _sent_selection(later._configuration_source) == (["b"], [])
    finally:
        first.join(timeout=5)
        provider.shutdown()
        later.shutdown()


def test_start_during_shutdown_does_not_start_a_second_poller(cdn, api_key, monkeypatch):
    provider = _provider({"tags": ["a"]})
    later = _provider({"tags": ["b"]})
    joining = threading.Event()
    resume = threading.Event()
    try:
        provider.initialize(EvaluationContext())
        source = provider._configuration_source
        join = source.join

        def blocking_join(*args, **kwargs):
            joining.set()
            resume.wait(timeout=5)
            return join(*args, **kwargs)

        monkeypatch.setattr(source, "join", blocking_join)
        stopping = threading.Thread(target=provider.shutdown)
        stopping.start()
        assert joining.wait(timeout=5)
        provider.initialize(EvaluationContext())
        assert provider._configuration_source is source
        resume.set()
        stopping.join(timeout=5)
        assert provider._configuration_source is None

        later.initialize(EvaluationContext())
        assert _sent_selection(later._configuration_source) == (["b"], [])
    finally:
        resume.set()
        provider.shutdown()
        later.shutdown()


def test_failed_poller_join_leaves_the_claim_to_the_next_shutdown(cdn, api_key, monkeypatch):
    provider = _provider({"tags": ["a"]})
    later = _provider({"tags": ["b"]})
    try:
        provider.initialize(EvaluationContext())
        source = provider._configuration_source
        join = source.join
        failures = [RuntimeError("join failed")]

        def join_failing_once(*args, **kwargs):
            if failures:
                raise failures.pop()
            return join(*args, **kwargs)

        monkeypatch.setattr(source, "join", join_failing_once)
        with pytest.raises(RuntimeError):
            provider.shutdown()
        assert provider._configuration_source is None
        assert provider._retired_configuration_sources == [source]

        provider.shutdown()
        assert provider._retired_configuration_sources == []
        later.initialize(EvaluationContext())
        assert _sent_selection(later._configuration_source) == (["b"], [])
    finally:
        provider.shutdown()
        later.shutdown()


def test_shutdown_from_inside_the_poll_returns_and_releases_the_claim_after_the_poll(cdn, api_key, monkeypatch):
    provider = _provider({"tags": ["a"]}, timeout=0.3)
    later = _provider({"tags": ["b"]})
    source = _poll_without_configuration(provider, cdn, monkeypatch)
    in_poll = []
    returned = threading.Event()

    def shut_down(*args, **kwargs):
        in_poll.append(source.polling_on_current_thread())
        provider.shutdown()
        returned.set()

    try:
        _on_recovery(provider, cdn, monkeypatch, shut_down)
        assert returned.wait(timeout=5), "shutdown() from inside the poll did not return"
        assert in_poll == [True]
        # The worker hands the poller over as it exits; the next provider start releases its claim.
        source.join(timeout=5)
        assert (provider, source) in provider_module._exited_configuration_sources
        later.initialize(EvaluationContext())
        assert _sent_selection(later._configuration_source) == (["b"], [])
        assert provider._retired_configuration_sources == []
    finally:
        later.shutdown()
        provider.shutdown()


def test_second_shutdown_during_the_poll_that_stopped_its_poller_releases_the_claim_once(cdn, api_key, monkeypatch):
    provider = _provider({"tags": ["a"]}, timeout=0.3)
    source = _poll_without_configuration(provider, cdn, monkeypatch)
    # Another reference to the claim, as a running provider with the same selection holds.
    claim_process_selection(canonicalize_flag_selection({"tags": ["a"]}))
    stopped, finish, joining = threading.Event(), threading.Event(), threading.Event()
    join = source.join

    def signaling_join(*args, **kwargs):
        joining.set()
        return join(*args, **kwargs)

    def shut_down_and_hold(*args, **kwargs):
        provider.shutdown()
        stopped.set()
        finish.wait(timeout=5)

    monkeypatch.setattr(source, "join", signaling_join)
    second = threading.Thread(target=provider.shutdown)
    try:
        _on_recovery(provider, cdn, monkeypatch, shut_down_and_hold)
        assert stopped.wait(timeout=5)
        assert not joining.is_set()
        assert provider._retired_configuration_sources == [source]
        assert _flag_selection._claim_count == 2
        second.start()
        # The second shutdown reserved the poller and waits for its poll to end.
        assert joining.wait(timeout=5)
        finish.set()
        second.join(timeout=5)
        assert not second.is_alive()
        assert provider._retired_configuration_sources == []
        assert _flag_selection._claim_count == 1
    finally:
        finish.set()
        if second.is_alive():
            second.join(timeout=5)
        provider.shutdown()


def test_lifecycle_events_stay_unpatched_when_the_application_patches_threading(cdn, api_key, monkeypatch):
    # gevent patches the application's threading; a callback on the poller thread can set these events.
    from ddtrace.internal import threads

    class _ApplicationEvent(threading.Event):
        pass

    class _ApplicationThreading:
        Event = _ApplicationEvent

        def __getattr__(self, name):
            return getattr(threading, name)

    monkeypatch.setattr(threads, "_app_threading", _ApplicationThreading())
    provider = _provider({"tags": ["a"]})
    try:
        provider.initialize(EvaluationContext())
        provider.shutdown()
        assert not isinstance(provider._configuration_source_changed, _ApplicationEvent)
        assert not isinstance(provider_module._lifecycle_ended, _ApplicationEvent)
    finally:
        provider.shutdown()


class _EscapingPoll(BaseException):
    """Escapes the poll's own error handling, which catches Exception."""


def test_poll_raising_after_a_shutdown_from_inside_it_still_releases_the_claim(cdn, api_key, monkeypatch):
    provider = _provider({"tags": ["a"]}, timeout=0.3)
    later = _provider({"tags": ["b"]})
    source = _poll_without_configuration(provider, cdn, monkeypatch)

    def shut_down_and_raise(*args, **kwargs):
        provider.shutdown()
        raise _EscapingPoll()

    try:
        _on_recovery(provider, cdn, monkeypatch, shut_down_and_raise)
        # The poll raises out of the worker, which then exits without on_shutdown.
        source.join(timeout=5)
        later.initialize(EvaluationContext())
        assert _sent_selection(later._configuration_source) == (["b"], [])
        assert provider._retired_configuration_sources == []
    finally:
        later.shutdown()
        provider.shutdown()


def test_poller_stopped_from_inside_its_poll_exits_while_another_thread_holds_the_locks(cdn, api_key, monkeypatch):
    from ddtrace.internal._threads import periodic_threads

    provider = _provider({"tags": ["a"]}, timeout=0.3)
    later = _provider({"tags": ["b"]})
    source = _poll_without_configuration(provider, cdn, monkeypatch)
    stopped, finish = threading.Event(), threading.Event()

    def shut_down_and_hold(*args, **kwargs):
        provider.shutdown()
        stopped.set()
        finish.wait(timeout=5)

    try:
        _on_recovery(provider, cdn, monkeypatch, shut_down_and_hold)
        assert stopped.wait(timeout=5)
        # A ddtrace thread takes no lock another thread holds, so the worker exits while this thread holds both.
        with provider._configuration_source_lock, _flag_selection._lock:
            finish.set()
            source.join(timeout=2)
            exited = all(thread is not source._worker for thread in list(periodic_threads.values()))
        assert exited
        later.initialize(EvaluationContext())
        assert _sent_selection(later._configuration_source) == (["b"], [])
    finally:
        finish.set()
        later.shutdown()
        provider.shutdown()


def test_start_waiting_for_another_threads_start_sleeps_instead_of_blocking(cdn, api_key, monkeypatch):
    # gevent patches time.sleep, so a start that waits in sleeps lets a green start on the same OS thread run.
    provider = _provider({"tags": ["a"]})
    resume = threading.Event()
    holding = _hold_source_creation(monkeypatch, resume)
    slept = threading.Event()
    real_time = provider_module.time

    class _ApplicationTime:
        monotonic = staticmethod(real_time.monotonic)
        time = staticmethod(real_time.time)

        @staticmethod
        def sleep(seconds):
            slept.set()
            real_time.sleep(seconds)

    first = threading.Thread(target=lambda: _initialize_quietly(provider))
    first.start()
    waiting = None
    try:
        assert holding.wait(timeout=5)
        monkeypatch.setattr(provider_module, "time", _ApplicationTime)
        waiting = threading.Thread(target=lambda: _initialize_quietly(provider))
        waiting.start()
        assert slept.wait(timeout=5)
        resume.set()
        waiting.join(timeout=5)
        assert not waiting.is_alive()
        assert provider._configuration_source is not None
    finally:
        resume.set()
        first.join(timeout=5)
        if waiting is not None:
            waiting.join(timeout=5)
        provider.shutdown()
