"""Flag selection: canonicalization, the request URL, the process claim and its diagnostics."""

import json
import logging
from pathlib import Path
import threading
from unittest import mock
from urllib.parse import parse_qsl
from urllib.parse import urlsplit

from openfeature.evaluation_context import EvaluationContext
from openfeature.exception import ProviderNotReadyError
from openfeature.provider import ProviderStatus
import pytest

from ddtrace.internal.openfeature import _flag_selection
from ddtrace.internal.openfeature._agentless import build_agentless_endpoint
import ddtrace.internal.openfeature._agentless_source as source_mod
from ddtrace.internal.openfeature._agentless_source import AgentlessConfigurationSource
from ddtrace.internal.openfeature._config import _get_ffe_snapshot
from ddtrace.internal.openfeature._config import _set_ffe_config
from ddtrace.internal.openfeature._flag_selection import NO_SELECTION
from ddtrace.internal.openfeature._flag_selection import append_flag_selection
from ddtrace.internal.openfeature._flag_selection import canonicalize_flag_selection
from ddtrace.internal.openfeature._flag_selection import claim_process_selection
from ddtrace.internal.openfeature._flag_selection import report_ignored
from ddtrace.internal.openfeature._source_selection import create_agentless_source
from ddtrace.internal.settings.openfeature import OpenFeatureConfig
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE
from ddtrace.openfeature import DataDogProvider
from tests.openfeature.config_helpers import create_boolean_flag
from tests.openfeature.config_helpers import create_config
from tests.utils import override_global_config


VECTORS_PATH = Path(__file__).parent / "ffe-system-test-data" / "flag-selection" / "selection-vectors.json"
VECTOR_CASES = json.loads(VECTORS_PATH.read_text(encoding="utf-8"))["cases"]

# Raw query strings exercise the server's percent-decoding, which the SDK never does.
RAW_QUERY_CASES = {
    "undecodable value is malformed",
    "undecodable unrelated parameter is not a selection error",
    "repeated parameters in a raw query are all read",
    "parameter names are percent-decoded",
    "malformed_selection takes precedence over too_many_values",
    "malformed_selection takes precedence over invalid_tag",
}

MANAGED_TARGET = "/api/v2/feature-flagging/config/rules-based/server?dd_env=prod"
SELECTION_LOGGER = "ddtrace.internal.openfeature._flag_selection"
PROVIDER_LOGGER = "ddtrace.internal.openfeature._provider"

NOT_SENT = "flag_selection was not sent ({}: {}). Loading the full flag configuration."
IGNORED = "flag_selection was ignored by the server ({}). Loaded the full flag configuration."
REMOTE_CONFIG_WARNING = (
    "flag_selection is not supported when flags are delivered through Remote Config. "
    "Loading the full flag configuration."
)
SECOND_SELECTION_WARNING = (
    "flag_selection differs from the flag selection already in use in this process. Using the first selection."
)
MALFORMED_LIMIT = "flag_selection must be a dict with optional tags and key_patterns string arrays"
TOO_MANY_LIMIT = "each list accepts at most 31 values"
KEY_PATTERN_LIMIT = "each key pattern is at most 200 UTF-8 bytes"
TAG_LIMIT = "each tag is at most 200 UTF-8 bytes"
URL_LIMIT = "the request URL is at most 8192 bytes"
LIMITS = {
    "malformed_selection": MALFORMED_LIMIT,
    "too_many_values": TOO_MANY_LIMIT,
    "invalid_key_pattern": KEY_PATTERN_LIMIT,
    "invalid_tag": TAG_LIMIT,
}


def _config(**env):
    return OpenFeatureConfig(source=env)


def _create_source(flag_selection=None, base_url=None, **env):
    if base_url is not None:
        env["DD_FEATURE_FLAGS_CONFIGURATION_SOURCE_AGENTLESS_BASE_URL"] = base_url
    with override_global_config({"_dd_api_key": "secret", "_dd_site": "datadoghq.com", "env": "prod"}):
        return create_agentless_source(_config(**env), lambda _: None, canonicalize_flag_selection(flag_selection))


def _sent_selection(source):
    """The selection a source requests, as (tags, key patterns) in request order."""
    pairs = parse_qsl(urlsplit(source._request_target).query, keep_blank_values=True)
    return (
        [value for name, value in pairs if name == "selected_flag_tags"],
        [value for name, value in pairs if name == "selected_flag_key_patterns"],
    )


def _not_sent(caplog):
    return [
        r
        for r in caplog.records
        if r.name == SELECTION_LOGGER and r.getMessage().startswith("flag_selection was not sent")
    ]


@pytest.fixture
def count_metric():
    with mock.patch.object(_flag_selection.telemetry_writer, "add_count_metric") as add_count_metric:
        yield add_count_metric


def _metric_calls(count_metric, name):
    return [c for c in count_metric.call_args_list if c.args[1] == name]


def test_vectors_have_the_expected_case_split():
    assert len(VECTOR_CASES) == 44
    assert {case["name"] for case in VECTOR_CASES if "rawQuery" in case} == RAW_QUERY_CASES
    assert sum(1 for case in VECTOR_CASES if "sdk" in case) == 38


@pytest.mark.parametrize("case", VECTOR_CASES, ids=[case["name"] for case in VECTOR_CASES])
def test_selection_vector(case, caplog, count_metric):
    if case["name"] in RAW_QUERY_CASES:
        pytest.skip("raw query cases cover server-side percent-decoding")

    query = case["query"]
    option = {"tags": query.get("selected_flag_tags"), "key_patterns": query.get("selected_flag_key_patterns")}
    with caplog.at_level(logging.ERROR, logger=SELECTION_LOGGER):
        source = _create_source(option)

    expected = case["sdk"]
    if "selection" in expected:
        selection = expected["selection"]
        assert _sent_selection(source) == (selection["tags"], selection["keyPatterns"])
        assert source._flag_selection_sent == bool(selection["tags"] or selection["keyPatterns"])
        assert _not_sent(caplog) == []
    else:
        assert source._request_target == MANAGED_TARGET
        assert source._flag_selection_sent is False
        assert [(r.levelno, r.getMessage()) for r in _not_sent(caplog)] == [
            (logging.ERROR, NOT_SENT.format(expected["dropped"], LIMITS[expected["dropped"]]))
        ]
        assert [c.args[3] for c in _metric_calls(count_metric, "flag_selection.dropped")] == [
            (("reason", expected["dropped"]),)
        ]


@pytest.mark.parametrize("separator", ["\x1c", "\x1d", "\x1e", "\x1f"])
def test_information_separators_are_not_trimmed(separator):
    value = f"{separator}team{separator}"
    assert canonicalize_flag_selection({"tags": [value], "key_patterns": [value]}) == (
        (value,),
        (value,),
        None,
    )


def test_only_unicode_white_space_is_trimmed():
    white_space = "\t\n\v\f\r \x85\xa0\u1680\u2000\u200a\u2028\u2029\u202f\u205f\u3000"
    selection = canonicalize_flag_selection({"tags": [f"{white_space}a{white_space}", "\ufeffb\u200b"]})
    assert selection.tags == ("a", "\ufeffb\u200b")


# Every code point with the Unicode White_Space property, from PropList.txt.
@pytest.mark.parametrize(
    "white_space",
    [
        "\u0009", "\u000a", "\u000b", "\u000c", "\u000d", "\u0020", "\u0085", "\u00a0", "\u1680",
        "\u2000", "\u2001", "\u2002", "\u2003", "\u2004", "\u2005", "\u2006", "\u2007", "\u2008",
        "\u2009", "\u200a", "\u2028", "\u2029", "\u202f", "\u205f", "\u3000",
    ],
)  # fmt: skip
def test_each_white_space_character_is_trimmed(white_space):
    assert canonicalize_flag_selection({"tags": [f"{white_space}a{white_space}"]}).tags == ("a",)


def test_values_are_deduplicated_and_sorted_by_utf8_bytes():
    selection = canonicalize_flag_selection({"tags": ["\U0001f600", "\uff5e", " b", "a", "b ", "", "  "]})
    # U+FF5E sorts before U+1F600 in UTF-8 but after it in UTF-16 code units.
    assert selection.tags == ("a", "b", "\uff5e", "\U0001f600")


@pytest.mark.parametrize("container", [list, tuple, set, frozenset])
def test_list_containers_are_accepted(container):
    selection = canonicalize_flag_selection({"tags": container(["a"]), "key_patterns": container(["b*"])})
    assert selection == (("a",), ("b*",), None)


@pytest.mark.parametrize(
    "option",
    [
        None,
        {},
        {"tags": None, "key_patterns": None},
        {"tags": [], "key_patterns": []},
        {"tags": [" ", "\u3000"], "key_patterns": [""]},
        {"keyPatterns": ["checkout.*"]},
        {"unknown": 1},
    ],
)
def test_absent_or_empty_selection_is_no_selection(option):
    assert canonicalize_flag_selection(option) == NO_SELECTION


def test_unknown_keys_are_ignored():
    selection = canonicalize_flag_selection({"tags": ["a"], "keyPatterns": ["checkout.*"], "extra": object()})
    assert selection == (("a",), (), None)


class _ExplodingMapping(dict):
    def get(self, key, default=None):
        raise RuntimeError("boom")


class _ExplodingList(list):
    def __iter__(self):
        raise RuntimeError("boom")


MALFORMED_OPTIONS = [
    "tags",
    b"tags",
    ["a"],
    12,
    object(),
    {"tags": "abc"},
    {"key_patterns": "checkout.*"},
    {"tags": {"a": 1}},
    {"tags": [1]},
    {"tags": [None]},
    {"tags": [b"a"]},
    {"tags": [True]},
    {"key_patterns": ["\ud800"]},
    {"tags": ["a\udfffb"]},
    {"tags": ["a"] * 40, "key_patterns": [None]},
    {"tags": ["a" * 201], "key_patterns": [3]},
    {"tags": [f"v{i}" for i in range(32)] + ["\ud800"]},
    {"tags": ["\udc00"], "key_patterns": ["x" * 201]},
]
EXPLODING_OPTIONS = [_ExplodingMapping(tags=["a"]), {"tags": _ExplodingList(["a"])}]


@pytest.mark.parametrize("option", MALFORMED_OPTIONS + EXPLODING_OPTIONS)
def test_malformed_selection(option):
    assert canonicalize_flag_selection(option).reason == "malformed_selection"


def test_limit_precedence():
    over_count = [f"v{i}" for i in range(32)]
    long_value = "x" * 201
    assert canonicalize_flag_selection({"tags": over_count, "key_patterns": [long_value]}).reason == "too_many_values"
    assert canonicalize_flag_selection({"tags": [long_value], "key_patterns": [long_value]}).reason == (
        "invalid_key_pattern"
    )
    assert canonicalize_flag_selection({"tags": [long_value]}).reason == "invalid_tag"
    assert canonicalize_flag_selection({"tags": ["\xe9" * 100]}).reason is None


def test_provider_copies_the_option():
    tags = ["a"]
    provider = DataDogProvider(flag_selection={"tags": tags})
    tags.append("b")
    assert provider._flag_selection == (("a",), (), None)


def test_values_are_percent_encoded_without_safe_characters():
    source = _create_source({"tags": ["a b/!'()*~-_.", "\xe9", "k=v&x"], "key_patterns": ["checkout.*"]})
    assert source._request_target == (
        MANAGED_TARGET + "&selected_flag_tags=a%20b%2F%21%27%28%29%2A~-_."
        "&selected_flag_tags=k%3Dv%26x"
        "&selected_flag_tags=%C3%A9"
        "&selected_flag_key_patterns=checkout.%2A"
    )


def test_selection_merges_into_a_custom_base_url_query():
    source = _create_source({"tags": ["a"]}, base_url="http://host.docker.internal:8126/ffe/rules?dd_env=test")
    assert source._request_target == "/ffe/rules?dd_env=test&selected_flag_tags=a"
    assert source._flag_selection_sent is True


def test_selection_starts_the_query_of_a_custom_base_url_without_one():
    source = _create_source({"key_patterns": ["a*"]}, base_url="http://host.docker.internal:8126")
    assert source._request_target == (
        "/api/v2/feature-flagging/config/rules-based/server?selected_flag_key_patterns=a%2A"
    )


@pytest.mark.parametrize("option", [None, {"tags": [" "], "key_patterns": []}])
def test_no_selection_leaves_the_request_unchanged(option, caplog, count_metric):
    with caplog.at_level(logging.DEBUG, logger=SELECTION_LOGGER):
        source = _create_source(option)
    assert source._request_target == MANAGED_TARGET
    assert source._flag_selection_sent is False
    assert [r for r in caplog.records if r.name == SELECTION_LOGGER] == []
    assert count_metric.call_args_list == []


def _option_with_url_length(endpoint, length):
    """A valid selection whose request URL is exactly ``length`` bytes long."""
    remaining = length - len(endpoint)
    tags = [f"tag{i:02d}".ljust(200, "x") for i in range(31)]
    remaining -= sum(len("&selected_flag_tags=") + len(tag) for tag in tags)
    overhead = len("&selected_flag_key_patterns=")
    key_patterns = []
    while remaining > overhead + 200:
        key_patterns.append(f"pattern{len(key_patterns):02d}".ljust(200, "x"))
        remaining -= overhead + 200
    last = remaining - overhead
    assert 9 <= last <= 200
    key_patterns.append(f"pattern{len(key_patterns):02d}".ljust(last, "x"))
    return {"tags": tags, "key_patterns": key_patterns}


@pytest.mark.parametrize(
    "base_url",
    [None, "http://flags.example.test:8126/custom/rules?dd_env=test#section"],
    ids=["managed", "custom"],
)
def test_request_url_limit(base_url, caplog, count_metric):
    endpoint = build_agentless_endpoint("datadoghq.com", "prod", base_url)

    at_limit = _option_with_url_length(endpoint, 8192)
    url = append_flag_selection(endpoint, canonicalize_flag_selection(at_limit))
    assert url is not None
    assert len(url.encode("utf-8")) == 8192
    with caplog.at_level(logging.ERROR, logger=SELECTION_LOGGER):
        source = _create_source(at_limit, base_url=base_url)
    assert source._flag_selection_sent is True
    assert len(_sent_selection(source)[1]) == len(at_limit["key_patterns"])
    assert _not_sent(caplog) == []

    _flag_selection._reset_flag_selection_state()
    over_limit = _option_with_url_length(endpoint, 8193)
    assert append_flag_selection(endpoint, canonicalize_flag_selection(over_limit)) is None
    with caplog.at_level(logging.ERROR, logger=SELECTION_LOGGER):
        source = _create_source(over_limit, base_url=base_url)
    assert source._flag_selection_sent is False
    assert _sent_selection(source) == ([], [])
    assert [r.getMessage() for r in _not_sent(caplog)] == [NOT_SENT.format("too_many_values", URL_LIMIT)]
    assert [c.args for c in count_metric.call_args_list] == [
        (TELEMETRY_NAMESPACE.TRACERS, "flag_selection.dropped", 1, (("reason", "too_many_values"),))
    ]


def test_request_url_limit_counts_the_fragment():
    endpoint = build_agentless_endpoint("datadoghq.com", None, "http://flags.example.test/rules#abc")
    option = _option_with_url_length(endpoint, 8192)
    assert append_flag_selection(endpoint, canonicalize_flag_selection(option)).endswith("#abc")
    longer = build_agentless_endpoint("datadoghq.com", None, "http://flags.example.test/rules#abcd")
    assert append_flag_selection(longer, canonicalize_flag_selection(option)) is None


@pytest.mark.parametrize(
    "option,reason,limit",
    [
        ({"tags": "secret-tag"}, "malformed_selection", MALFORMED_LIMIT),
        ({"tags": [f"secret-tag-{i}" for i in range(32)]}, "too_many_values", TOO_MANY_LIMIT),
        ({"key_patterns": ["secret-" + "p" * 200]}, "invalid_key_pattern", KEY_PATTERN_LIMIT),
        ({"tags": ["secret-" + "t" * 200]}, "invalid_tag", TAG_LIMIT),
    ],
)
def test_dropped_selection_is_logged_and_counted(option, reason, limit, caplog, count_metric):
    with caplog.at_level(logging.ERROR, logger=SELECTION_LOGGER):
        source = _create_source(option)
    assert source._request_target == MANAGED_TARGET
    assert [(r.levelno, r.getMessage()) for r in _not_sent(caplog)] == [(logging.ERROR, NOT_SENT.format(reason, limit))]
    assert "secret" not in caplog.text
    assert [c.args for c in count_metric.call_args_list] == [
        (TELEMETRY_NAMESPACE.TRACERS, "flag_selection.dropped", 1, (("reason", reason),))
    ]


def test_equal_dropped_selection_logs_per_provider_and_counts_once(caplog, count_metric):
    with caplog.at_level(logging.ERROR, logger=SELECTION_LOGGER):
        _create_source({"tags": [f"t{i}" for i in range(32)]})
        _create_source({"tags": [f" t{i} " for i in range(32)] + ["t0"]})
    assert len(_not_sent(caplog)) == 2
    assert len(_metric_calls(count_metric, "flag_selection.dropped")) == 1


def test_different_selection_than_a_dropped_claim_is_not_reported_as_dropped(caplog, count_metric):
    with caplog.at_level(logging.WARNING, logger=SELECTION_LOGGER):
        _create_source({"tags": "malformed"})
        source = _create_source({"tags": ["a"]})
    assert source._request_target == MANAGED_TARGET
    assert len(_not_sent(caplog)) == 1
    assert caplog.messages.count(SECOND_SELECTION_WARNING) == 1


def test_two_reasons_within_the_rate_limit_window_are_both_logged(caplog):
    # A logger at DEBUG bypasses the rate limiter, so keep it above DEBUG.
    with caplog.at_level(logging.WARNING, logger=SELECTION_LOGGER):
        assert logging.getLogger(SELECTION_LOGGER).getEffectiveLevel() != logging.DEBUG
        _create_source({"tags": "malformed"})
        _flag_selection.release_process_selection()
        _create_source({"tags": ["x" * 201]})
        _flag_selection.release_process_selection()
        _create_source({"tags": "malformed"})
    assert [r.getMessage() for r in _not_sent(caplog)] == [
        NOT_SENT.format("malformed_selection", MALFORMED_LIMIT),
        NOT_SENT.format("invalid_tag", TAG_LIMIT),
        NOT_SENT.format("malformed_selection", MALFORMED_LIMIT),
    ]


def _ufc_body():
    return json.dumps(
        {"data": {"id": "1", "type": "universal-flag-configuration", "attributes": create_config()}}
    ).encode("utf-8")


class _FakeResponse:
    def __init__(self, status, body=b"", headers=None):
        self.status = status
        self._body = body
        self._headers = {k.lower(): v for k, v in (headers or {}).items()}
        self.read_headers: list = []

    def read(self):
        return self._body

    def getheader(self, name, default=None):
        self.read_headers.append(name.lower())
        return self._headers.get(name.lower(), default)


class _FakeConn:
    def __init__(self, respond, requests):
        self._respond = respond
        self._requests = requests
        self._target = None

    def request(self, method, target, body, headers):
        self._target = target
        self._requests.append(target)

    def getresponse(self):
        return self._respond(self._target)

    def close(self):
        pass


@pytest.fixture
def poller(monkeypatch):
    def build(responses, flag_selection_sent=True, apply_configuration=None):
        requests: list = []
        applied: list = []
        monkeypatch.setattr(
            source_mod, "get_connection", lambda url, timeout=None: _FakeConn(lambda _: responses.pop(0), requests)
        )
        source = AgentlessConfigurationSource(
            endpoint="https://ufc-server.ff-cdn.datadoghq.com" + MANAGED_TARGET + "&selected_flag_tags=a",
            apply_configuration=apply_configuration or applied.append,
            flag_selection_sent=flag_selection_sent,
        )
        monkeypatch.setattr(source, "_retry_delay", lambda attempt: 0.0)
        source._applied = applied
        return source

    return build


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


def test_later_different_selection_uses_the_first(caplog, count_metric):
    with caplog.at_level(logging.WARNING, logger=SELECTION_LOGGER):
        first = _create_source({"tags": ["a"]})
        same = _create_source({"tags": [" a", "a"]})
        second = _create_source({"tags": ["b"]})
        third = _create_source({"key_patterns": ["c*"]})
        none = _create_source(None)
    for source in (first, same, second, third, none):
        assert _sent_selection(source) == (["a"], [])
    assert [r.getMessage() for r in caplog.records if r.name == SELECTION_LOGGER] == [SECOND_SELECTION_WARNING]
    assert count_metric.call_args_list == []


def test_no_selection_claims_all_flags(caplog):
    with caplog.at_level(logging.WARNING, logger=SELECTION_LOGGER):
        _create_source(None)
        source = _create_source({"tags": ["a"]})
    assert source._request_target == MANAGED_TARGET
    assert caplog.messages.count(SECOND_SELECTION_WARNING) == 1


def test_claim_is_released_with_its_last_reference():
    a = canonicalize_flag_selection({"tags": ["a"]})
    b = canonicalize_flag_selection({"tags": ["b"]})
    assert claim_process_selection(a) == a
    assert claim_process_selection(b) == a
    _flag_selection.release_process_selection()
    assert claim_process_selection(b) == a
    _flag_selection.release_process_selection()
    _flag_selection.release_process_selection()
    assert claim_process_selection(b) == b


def test_loaded_selection_is_recorded_only_after_a_successful_apply():
    a = canonicalize_flag_selection({"tags": ["a"]})
    b = canonicalize_flag_selection({"tags": ["b"]})
    _flag_selection.recording_loaded_selection(lambda _: None, a)({})
    claim_process_selection(b)

    def reject(_):
        raise ValueError("rejected")

    with pytest.raises(ValueError):
        _flag_selection.recording_loaded_selection(reject, b)({})
    assert _flag_selection.snapshot_from_other_selection() is True


def test_missing_api_key_takes_no_claim():
    with override_global_config({"_dd_api_key": None, "_dd_site": "datadoghq.com", "env": "prod"}):
        assert create_agentless_source(_config(), lambda _: None, canonicalize_flag_selection({"tags": ["a"]})) is None
    assert claim_process_selection(canonicalize_flag_selection({"tags": ["z"]})).tags == ("z",)


def test_invalid_base_url_takes_no_claim():
    assert _create_source({"tags": ["a"]}, base_url="ftp://flags.example") is None
    assert claim_process_selection(canonicalize_flag_selection({"tags": ["z"]})).tags == ("z",)


def test_claim_is_released_when_the_source_cannot_be_built(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("cannot build")

    monkeypatch.setattr(AgentlessConfigurationSource, "__init__", fail)
    with pytest.raises(RuntimeError):
        _create_source({"tags": ["a"]})
    assert claim_process_selection(canonicalize_flag_selection({"tags": ["z"]})).tags == ("z",)


def test_concurrent_claims_agree():
    selections = [canonicalize_flag_selection({"tags": [str(i)]}) for i in range(8)]
    results = []
    barrier = threading.Barrier(len(selections))

    def claim(selection):
        barrier.wait()
        results.append(claim_process_selection(selection))

    threads = [threading.Thread(target=claim, args=(s,)) for s in selections]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(set(results)) == 1


@pytest.fixture
def cdn(monkeypatch):
    """Serve every agentless poll from a swappable responder and record request targets."""

    class _Cdn:
        requests: list = []

        def __init__(self):
            self.respond = lambda target: _FakeResponse(200, _ufc_body())

    server = _Cdn()
    monkeypatch.setattr(
        source_mod,
        "get_connection",
        lambda url, timeout=None: _FakeConn(lambda target: server.respond(target), server.requests),
    )
    _set_ffe_config(None)
    yield server
    _set_ffe_config(None)


def _flag_body(flag_key):
    return json.dumps(
        {
            "data": {
                "id": "1",
                "type": "universal-flag-configuration",
                "attributes": create_config(create_boolean_flag(flag_key, enabled=True, default_value=True)),
            }
        }
    ).encode("utf-8")


@pytest.fixture
def api_key():
    with override_global_config({"_dd_api_key": "secret", "_dd_site": "datadoghq.com", "env": None}):
        yield


def _provider(flag_selection, timeout=5.0):
    return DataDogProvider(initialization_timeout=timeout, flag_selection=flag_selection)


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


# err=None: CPython 3.12+ warns on stderr when a multi-threaded process forks; the child's exit status is the result.
@pytest.mark.subprocess(err=None)
def test_once_per_process_guards_reset_in_a_forked_child():
    import os
    from unittest import mock

    from ddtrace.internal.openfeature import _flag_selection
    from ddtrace.internal.openfeature._flag_selection import canonicalize_flag_selection
    from ddtrace.internal.openfeature._flag_selection import claim_process_selection
    from ddtrace.internal.openfeature._flag_selection import report_ignored
    from ddtrace.internal.openfeature._flag_selection import warn_remote_config

    claimed = canonicalize_flag_selection({"tags": ["a"]})
    with (
        mock.patch.object(_flag_selection, "log"),
        mock.patch.object(_flag_selection.telemetry_writer, "add_count_metric"),
    ):
        claim_process_selection(claimed)
        claim_process_selection(canonicalize_flag_selection({"tags": ["b"]}))
        report_ignored("tag_load_failed")
        warn_remote_config(claimed)

    pid = os.fork()
    if pid == 0:
        with (
            mock.patch.object(_flag_selection, "log") as log,
            mock.patch.object(_flag_selection.telemetry_writer, "add_count_metric") as add_count_metric,
        ):
            inherited = claim_process_selection(canonicalize_flag_selection({"tags": ["c"]}))
            report_ignored("tag_load_failed")
            warn_remote_config(claimed)
        reset = (
            inherited == claimed
            and add_count_metric.call_count == 1
            and log.error.call_count == 1
            and log.warning.call_count == 2
        )
        os._exit(0 if reset else 1)

    _, status = os.waitpid(pid, 0)
    assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0
