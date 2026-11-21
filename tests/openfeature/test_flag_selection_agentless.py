"""Flag selection on the agentless poller: the ignored-selection header, rejected requests, Remote Config."""

import http.server
import logging
import threading
from unittest import mock

import pytest

from ddtrace.internal.openfeature import _flag_selection
import ddtrace.internal.openfeature._agentless_source as source_mod
from ddtrace.internal.openfeature._agentless_source import AgentlessConfigurationSource
from ddtrace.internal.openfeature._flag_selection import canonicalize_flag_selection
from ddtrace.internal.openfeature._flag_selection import claim_process_selection
from ddtrace.internal.openfeature._flag_selection import report_ignored
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE
from tests.openfeature.flag_selection_helpers import IGNORED
from tests.openfeature.flag_selection_helpers import MANAGED_TARGET
from tests.openfeature.flag_selection_helpers import REMOTE_CONFIG_WARNING
from tests.openfeature.flag_selection_helpers import SELECTION_LOGGER
from tests.openfeature.flag_selection_helpers import _create_source
from tests.openfeature.flag_selection_helpers import _FakeResponse
from tests.openfeature.flag_selection_helpers import _metric_calls
from tests.openfeature.flag_selection_helpers import _ufc_body


def _ignored(caplog):
    return [r for r in caplog.records if r.getMessage().startswith("flag_selection was ignored")]


@pytest.mark.parametrize(
    "header,reason",
    [
        ("malformed_selection", "malformed_selection"),
        ("too_many_values", "too_many_values"),
        ("invalid_key_pattern", "invalid_key_pattern"),
        ("invalid_tag", "invalid_tag"),
        ("tag_load_failed", "tag_load_failed"),
        ("tag_load_failed ", "unknown"),
        ("invalid_tag, tag_load_failed", "unknown"),
        (["too_many_values", "invalid_tag"], "unknown"),
        (["tag_load_failed", "tag_load_failed"], "unknown"),
        ("Invalid_Tag", "unknown"),
        ("x" * 33, "unknown"),
        ("something_new", "unknown"),
    ],
)
def test_ignored_header_is_sanitized(poller, caplog, count_metric, header, reason):
    source = poller([_FakeResponse(200, _ufc_body(), {"DD-Flag-Selection-Ignored": header})])
    with caplog.at_level(logging.ERROR, logger=SELECTION_LOGGER):
        source.periodic()
    assert len(source._applied) == 1
    assert [(r.levelno, r.getMessage()) for r in _ignored(caplog)] == [(logging.ERROR, IGNORED.format(reason))]
    assert [c.args for c in count_metric.call_args_list] == [
        (TELEMETRY_NAMESPACE.TRACERS, "flag_selection.ignored", 1, (("reason", reason),))
    ]


@pytest.mark.parametrize(
    "values,reason",
    [
        (["too_many_values", "invalid_tag"], "unknown"),
        # The native client strips surrounding whitespace before the sanitizer sees the value.
        (["tag_load_failed \t"], "tag_load_failed"),
    ],
)
def test_ignored_header_through_the_native_client(caplog, count_metric, values, reason):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = _ufc_body()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            for value in values:
                self.send_header("DD-Flag-Selection-Ignored", value)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    applied: list = []
    try:
        source = AgentlessConfigurationSource(
            endpoint=f"http://127.0.0.1:{server.server_address[1]}{MANAGED_TARGET}&selected_flag_tags=a",
            apply_configuration=applied.append,
            flag_selection_sent=True,
        )
        with caplog.at_level(logging.ERROR, logger=SELECTION_LOGGER):
            source.periodic()
    finally:
        server.shutdown()
        server.server_close()
    assert len(applied) == 1
    assert [r.getMessage() for r in _ignored(caplog)] == [IGNORED.format(reason)]
    assert [c.args[3] for c in count_metric.call_args_list] == [(("reason", reason),)]


def test_empty_ignored_header_is_absent(poller, caplog, count_metric):
    source = poller([_FakeResponse(200, _ufc_body(), {"DD-Flag-Selection-Ignored": ""})])
    with caplog.at_level(logging.ERROR, logger=SELECTION_LOGGER):
        source.periodic()
    assert _ignored(caplog) == []
    assert count_metric.call_args_list == []


def test_ignored_header_is_reported_once_per_reason(poller, caplog, count_metric):
    responses = [
        _FakeResponse(200, _ufc_body(), {"DD-Flag-Selection-Ignored": "tag_load_failed"}),
        _FakeResponse(200, _ufc_body(), {"DD-Flag-Selection-Ignored": "tag_load_failed"}),
        _FakeResponse(200, _ufc_body(), {"DD-Flag-Selection-Ignored": "invalid_tag"}),
        _FakeResponse(200, _ufc_body(), {"DD-Flag-Selection-Ignored": "bogus"}),
        _FakeResponse(200, _ufc_body(), {"DD-Flag-Selection-Ignored": "other"}),
    ]
    source = poller(responses)
    # Above DEBUG so the per-call-site rate limiter is active.
    with caplog.at_level(logging.ERROR, logger=SELECTION_LOGGER):
        for _ in range(5):
            source.periodic()
    assert [r.getMessage() for r in _ignored(caplog)] == [
        IGNORED.format("tag_load_failed"),
        IGNORED.format("invalid_tag"),
        IGNORED.format("unknown"),
    ]
    assert [c.args[3] for c in count_metric.call_args_list] == [
        (("reason", "tag_load_failed"),),
        (("reason", "invalid_tag"),),
        (("reason", "unknown"),),
    ]


def test_ignored_header_is_reported_after_the_configuration_is_applied(poller, monkeypatch):
    events = []
    source = poller(
        [_FakeResponse(200, _ufc_body(), {"DD-Flag-Selection-Ignored": "tag_load_failed"})],
        apply_configuration=lambda _: events.append("applied"),
    )
    monkeypatch.setattr(source_mod, "report_ignored", lambda value: events.append(("reported", value)))
    source.periodic()
    assert events == ["applied", ("reported", "tag_load_failed")]


@pytest.mark.parametrize("failure", ["apply", "payload"])
def test_ignored_header_is_not_reported_when_the_configuration_is_not_applied(poller, caplog, count_metric, failure):
    def reject(_):
        raise ValueError("rejected")

    body = b"{not json" if failure == "payload" else _ufc_body()
    source = poller(
        [_FakeResponse(200, body, {"DD-Flag-Selection-Ignored": "tag_load_failed"})],
        apply_configuration=reject if failure == "apply" else None,
    )
    with caplog.at_level(logging.ERROR, logger=SELECTION_LOGGER):
        source.periodic()
    assert _ignored(caplog) == []
    assert count_metric.call_args_list == []


@pytest.mark.parametrize(
    "status,flag_selection_sent",
    [(304, True), (404, True), (500, True), (200, False)],
)
def test_ignored_header_is_read_only_on_200_with_a_selection(poller, caplog, count_metric, status, flag_selection_sent):
    responses = [_FakeResponse(status, _ufc_body(), {"DD-Flag-Selection-Ignored": "tag_load_failed"}) for _ in range(3)]
    source = poller(list(responses), flag_selection_sent=flag_selection_sent)
    with caplog.at_level(logging.ERROR, logger=SELECTION_LOGGER):
        source.periodic()
    assert all("dd-flag-selection-ignored" not in r.read_headers for r in responses)
    assert _ignored(caplog) == []
    assert count_metric.call_args_list == []


@pytest.mark.parametrize("status", [400, 414])
@pytest.mark.parametrize("flag_selection_sent", [True, False])
def test_rejected_request_names_the_selection_as_a_cause(poller, caplog, status, flag_selection_sent):
    source = poller([_FakeResponse(status)], flag_selection_sent=flag_selection_sent)
    # DEBUG turns the per-call-site rate limiter off across the parametrized runs.
    with caplog.at_level(logging.DEBUG, logger=source_mod.__name__):
        source.periodic()
    [message] = [
        r.getMessage() for r in caplog.records if r.name == source_mod.__name__ and r.levelno == logging.WARNING
    ]
    assert message.startswith(f"Feature Flagging agentless endpoint returned HTTP {status}. Possible causes: ")
    assert ("the flag_selection query" in message) is flag_selection_sent


def test_report_ignored_never_raises(count_metric):
    count_metric.side_effect = RuntimeError("telemetry down")
    report_ignored("tag_load_failed")


@pytest.mark.parametrize(
    "env",
    [
        {"DD_FEATURE_FLAGS_CONFIGURATION_SOURCE": "remote_config"},
        {"DD_EXPERIMENTAL_FLAGGING_PROVIDER_ENABLED": "true"},
    ],
)
def test_remote_config_warns_once_when_a_selection_is_set(env, caplog, count_metric):
    with caplog.at_level(logging.WARNING, logger=SELECTION_LOGGER):
        assert _create_source({"tags": ["a"]}, **env) is None
        assert _create_source({"tags": "malformed"}, **env) is None
    assert [(r.levelno, r.getMessage()) for r in caplog.records if r.name == SELECTION_LOGGER] == [
        (logging.WARNING, REMOTE_CONFIG_WARNING)
    ]
    assert count_metric.call_args_list == []
    assert claim_process_selection(canonicalize_flag_selection({"tags": ["z"]})).tags == ("z",)


@pytest.mark.parametrize("option", [None, {"tags": [" "]}])
def test_remote_config_does_not_warn_without_a_selection(option, caplog):
    with caplog.at_level(logging.WARNING, logger=SELECTION_LOGGER):
        assert _create_source(option, DD_FEATURE_FLAGS_CONFIGURATION_SOURCE="remote_config") is None
    assert [r for r in caplog.records if r.name == SELECTION_LOGGER] == []


def test_poll_does_not_wait_for_the_selection_lock(poller, count_metric):
    # Pollers never wait on each other: a poll counts an inherited drop, records what it applied and reports an
    # ignored selection while another thread holds the selection lock.
    with mock.patch.object(_flag_selection, "log"):
        _create_source({"tags": ["t" * 201]})
    _flag_selection._reset_once_per_process_guards()  # as in a forked child that inherited the dropped claim
    count_metric.reset_mock()
    selection = canonicalize_flag_selection({"tags": ["a"]})
    applied = []
    source = poller(
        [_FakeResponse(200, _ufc_body(), {"DD-Flag-Selection-Ignored": "tag_load_failed"})],
        apply_configuration=_flag_selection.recording_loaded_selection(applied.append, selection, lambda: None),
    )
    polled = threading.Event()

    def poll():
        source.periodic()
        polled.set()

    polling = threading.Thread(target=poll)
    with _flag_selection._lock:
        polling.start()
        assert polled.wait(timeout=5)
    polling.join(timeout=5)
    assert len(applied) == 1
    assert _flag_selection._loaded == selection
    assert len(_metric_calls(count_metric, "flag_selection.dropped")) == 1
    assert len(_metric_calls(count_metric, "flag_selection.ignored")) == 1
