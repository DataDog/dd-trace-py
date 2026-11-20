"""Flag selection readiness after a claim is released and another selection takes over."""

import json
import logging
import threading

from openfeature.evaluation_context import EvaluationContext
from openfeature.exception import ProviderNotReadyError
from openfeature.provider import ProviderStatus
import pytest

from ddtrace.internal.openfeature._agentless import build_agentless_endpoint
from ddtrace.internal.openfeature._config import _get_ffe_snapshot
from tests.openfeature.flag_selection_helpers import EXPLODING_OPTIONS
from tests.openfeature.flag_selection_helpers import MALFORMED_OPTIONS
from tests.openfeature.flag_selection_helpers import PROVIDER_LOGGER
from tests.openfeature.flag_selection_helpers import SELECTION_LOGGER
from tests.openfeature.flag_selection_helpers import _FakeResponse
from tests.openfeature.flag_selection_helpers import _flag_body
from tests.openfeature.flag_selection_helpers import _metric_calls
from tests.openfeature.flag_selection_helpers import _not_sent
from tests.openfeature.flag_selection_helpers import _option_with_url_length
from tests.openfeature.flag_selection_helpers import _provider
from tests.openfeature.flag_selection_helpers import _SignalingEvent
from tests.utils import override_global_config


def test_new_selection_after_release_waits_for_its_own_poll(cdn, api_key, caplog):
    cdn.respond = lambda target: _FakeResponse(200, _flag_body("selected-a"))
    first = _provider({"tags": ["a"]})
    first.initialize(EvaluationContext())
    first.shutdown()
    assert _get_ffe_snapshot() is not None

    cdn.respond = lambda target: _FakeResponse(404)
    other = _provider({"tags": ["b"]}, timeout=0.2)
    try:
        with caplog.at_level(logging.DEBUG, logger=PROVIDER_LOGGER):
            with pytest.raises(ProviderNotReadyError):
                other.initialize(EvaluationContext())
        assert other._status == ProviderStatus.NOT_READY
        assert any(
            r.levelno == logging.WARNING and "configuration loaded for the previous flag selection" in r.getMessage()
            for r in caplog.records
        )
        # Until its own configuration arrives, the provider resolves against the previous snapshot.
        assert other.resolve_boolean_details("selected-a", False).value is True
    finally:
        other.shutdown()

    cdn.respond = lambda target: _FakeResponse(200, _flag_body("selected-b"))
    recovering = _provider({"tags": ["b"]})
    try:
        recovering.initialize(EvaluationContext())
        assert recovering._status == ProviderStatus.READY
        assert recovering.resolve_boolean_details("selected-b", False).value is True
    finally:
        recovering.shutdown()


def test_notification_from_the_released_selection_does_not_make_a_new_selection_ready(cdn, api_key, monkeypatch):
    import ddtrace.internal.openfeature._source_selection as source_selection

    cdn.respond = lambda target: _FakeResponse(200, _flag_body("selected-a"))
    first = _provider({"tags": ["a"]})
    first.initialize(EvaluationContext())
    stale_apply = first._configuration_source._apply_configuration
    attributes = json.loads(_flag_body("selected-a"))["data"]["attributes"]
    claim = source_selection.claim_process_selection

    # The released provider's poller applies a configuration after the new provider registered
    # for notifications, then shuts down before the new provider claims.
    def claim_after_a_late_apply(selection):
        stale_apply(attributes)
        first.shutdown()
        return claim(selection)

    monkeypatch.setattr(source_selection, "claim_process_selection", claim_after_a_late_apply)
    cdn.respond = lambda target: _FakeResponse(404)
    second = _provider({"tags": ["b"]}, timeout=0.2)
    try:
        with pytest.raises(ProviderNotReadyError):
            second.initialize(EvaluationContext())
        assert second._status == ProviderStatus.NOT_READY
    finally:
        second.shutdown()


def test_same_selection_after_release_uses_the_loaded_configuration(cdn, api_key):
    cdn.respond = lambda target: _FakeResponse(200, _flag_body("selected-a"))
    first = _provider({"tags": ["a"]})
    first.initialize(EvaluationContext())
    first.shutdown()

    cdn.respond = lambda target: _FakeResponse(404)
    same = _provider({"tags": ["a "]}, timeout=0.2)
    try:
        same.initialize(EvaluationContext())
        assert same._status == ProviderStatus.READY
    finally:
        same.shutdown()


_OVER_THE_URL_LIMIT = _option_with_url_length(build_agentless_endpoint("datadoghq.com", None, None), 8193)


@pytest.mark.parametrize(
    "first_option,later_option",
    [
        ({"tags": "malformed"}, None),
        (None, {"tags": ["x" * 201]}),
        (_OVER_THE_URL_LIMIT, {}),
        ({"tags": "malformed"}, _OVER_THE_URL_LIMIT),
    ],
    ids=["malformed-then-none", "none-then-invalid-tag", "url-limit-then-none", "malformed-then-url-limit"],
)
def test_dropped_and_no_selection_after_release_use_the_loaded_configuration(cdn, api_key, first_option, later_option):
    first = _provider(first_option)
    first.initialize(EvaluationContext())
    first.shutdown()

    cdn.respond = lambda target: _FakeResponse(404)
    later = _provider(later_option, timeout=0.2)
    try:
        later.initialize(EvaluationContext())
        assert later._status == ProviderStatus.READY
    finally:
        later.shutdown()


def test_sent_selection_after_a_dropped_one_waits_for_its_own_poll(cdn, api_key):
    first = _provider({"tags": "malformed"})
    first.initialize(EvaluationContext())
    first.shutdown()

    cdn.respond = lambda target: _FakeResponse(404)
    later = _provider({"tags": ["a"]}, timeout=0.2)
    try:
        with pytest.raises(ProviderNotReadyError):
            later.initialize(EvaluationContext())
    finally:
        later.shutdown()


def test_provider_starting_while_the_new_selection_is_applied_becomes_ready(cdn, api_key):
    from ddtrace.internal.openfeature._config import _register_provider
    from ddtrace.internal.openfeature._config import _unregister_provider

    cdn.respond = lambda target: _FakeResponse(200, _flag_body("selected-a"))
    first = _provider({"tags": ["a"]})
    first.initialize(EvaluationContext())
    first.shutdown()

    # Only the second provider's first poll succeeds; the third provider's own poller keeps failing.
    responses = [_FakeResponse(200, _flag_body("selected-b"))]
    cdn.respond = lambda target: responses.pop(0) if responses else _FakeResponse(404)
    second = _provider({"tags": ["b"]})
    third = _provider({"tags": ["b"]}, timeout=1.0)
    third._config_received = _SignalingEvent()
    outcome = []

    def initialize_third():
        try:
            third.initialize(EvaluationContext())
            outcome.append(third._status)
        except ProviderNotReadyError as error:
            outcome.append(error)

    class _StartsThirdDuringTheApply:
        thread = None

        def on_configuration_received(self):
            # Runs while the second provider's poller applies the new selection, before it is recorded.
            if self.thread is None:
                self.thread = threading.Thread(target=initialize_third)
                self.thread.start()
                assert third._config_received.waiting.wait(timeout=5)

    spy = _StartsThirdDuringTheApply()
    _register_provider(third)
    _register_provider(spy)
    try:
        second.initialize(EvaluationContext())
        spy.thread.join(timeout=5)
        assert outcome == [ProviderStatus.READY]
    finally:
        _unregister_provider(spy)
        if spy.thread is not None:
            spy.thread.join(timeout=5)
        second.shutdown()
        third.shutdown()


def test_not_sent_is_logged_once_per_provider_initialization(cdn, api_key, caplog, count_metric):
    provider = _provider({"tags": "malformed"})
    other = _provider({"tags": "also malformed"})
    try:
        with caplog.at_level(logging.ERROR, logger=SELECTION_LOGGER):
            provider.initialize(EvaluationContext())
            provider.initialize(EvaluationContext())
            other.initialize(EvaluationContext())
            provider.shutdown()
            provider.initialize(EvaluationContext())
        assert len(_not_sent(caplog)) == 3
        assert len(_metric_calls(count_metric, "flag_selection.dropped")) == 1
    finally:
        provider.shutdown()
        other.shutdown()


@pytest.mark.parametrize("option", MALFORMED_OPTIONS[:6] + EXPLODING_OPTIONS)
def test_any_flag_selection_value_reaches_ready(cdn, api_key, option):
    provider = _provider(option)
    try:
        provider.initialize(EvaluationContext())
        assert provider._status == ProviderStatus.READY
        assert cdn.requests[-1] == "/api/v2/feature-flagging/config/rules-based/server"
    finally:
        provider.shutdown()


def test_provider_whose_source_cannot_start_fails_on_a_released_selections_snapshot(cdn, api_key):
    first = _provider({"tags": ["a"]})
    replacement = _provider({"tags": ["b"]})
    try:
        first.initialize(EvaluationContext())
        first.shutdown()
        assert _get_ffe_snapshot() is not None
        with override_global_config({"_dd_api_key": None, "_dd_site": "datadoghq.com", "env": None}):
            with pytest.raises(ProviderNotReadyError, match="DD_API_KEY"):
                replacement.initialize(EvaluationContext())
        assert replacement._status != ProviderStatus.READY
    finally:
        replacement.shutdown()
        first.shutdown()


def test_provider_whose_source_cannot_start_uses_a_released_snapshot_with_its_flags(cdn, api_key):
    first = _provider({"tags": ["a"]})
    same = _provider({"tags": ["a"]})
    try:
        first.initialize(EvaluationContext())
        first.shutdown()
        with override_global_config({"_dd_api_key": None, "_dd_site": "datadoghq.com", "env": None}):
            same.initialize(EvaluationContext())
        assert same._status == ProviderStatus.READY
    finally:
        same.shutdown()
        first.shutdown()
