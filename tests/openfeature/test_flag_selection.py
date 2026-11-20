"""Flag selection: canonicalization, validation, the request URL, drop reporting and the process claim."""

import json
import logging
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from ddtrace.internal.openfeature import _flag_selection
from ddtrace.internal.openfeature._agentless import build_agentless_endpoint
from ddtrace.internal.openfeature._agentless_source import AgentlessConfigurationSource
from ddtrace.internal.openfeature._flag_selection import NO_SELECTION
from ddtrace.internal.openfeature._flag_selection import append_flag_selection
from ddtrace.internal.openfeature._flag_selection import canonicalize_flag_selection
from ddtrace.internal.openfeature._flag_selection import claim_process_selection
import ddtrace.internal.openfeature._native as native_mod
from ddtrace.internal.openfeature._source_selection import MissingApiKeyError
from ddtrace.internal.openfeature._source_selection import create_agentless_source
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE
from ddtrace.openfeature import DataDogProvider
from tests.openfeature.flag_selection_helpers import EXPLODING_OPTIONS
from tests.openfeature.flag_selection_helpers import MALFORMED_OPTIONS
from tests.openfeature.flag_selection_helpers import MANAGED_TARGET
from tests.openfeature.flag_selection_helpers import SECOND_SELECTION_WARNING
from tests.openfeature.flag_selection_helpers import SELECTION_LOGGER
from tests.openfeature.flag_selection_helpers import _config
from tests.openfeature.flag_selection_helpers import _create_source
from tests.openfeature.flag_selection_helpers import _metric_calls
from tests.openfeature.flag_selection_helpers import _not_sent
from tests.openfeature.flag_selection_helpers import _option_with_url_length
from tests.openfeature.flag_selection_helpers import _sent_selection
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


NOT_SENT = "flag_selection was not sent ({}: {}). Loading the full flag configuration."


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


CAPABILITY_PARAMETER = "&cap_dependent-flags-variant-key=v1"


@pytest.mark.parametrize(
    "capabilities,url_length,sent",
    [
        ((), 8192, True),
        ((native_mod.DEPENDENT_FLAG_VARIANT_KEY_V1,), 8192, False),
        ((native_mod.DEPENDENT_FLAG_VARIANT_KEY_V1,), 8192 - len(CAPABILITY_PARAMETER), True),
    ],
    ids=["unsupported", "supported-over-limit", "supported-at-limit"],
)
def test_request_url_limit_counts_the_capability_parameter(
    monkeypatch, caplog, count_metric, capabilities, url_length, sent
):
    monkeypatch.setattr(native_mod, "ffe", SimpleNamespace(get_evaluator_capabilities=lambda: capabilities))
    endpoint = build_agentless_endpoint("datadoghq.com", "prod", None)
    option = _option_with_url_length(endpoint, url_length)
    with caplog.at_level(logging.ERROR, logger=SELECTION_LOGGER):
        source = _create_source(option)

    assert source._flag_selection_sent is sent
    assert source._request_target.endswith(CAPABILITY_PARAMETER) is bool(capabilities)
    if sent:
        assert len(source._conn_url.rstrip("/") + source._request_target) == 8192
        assert len(_sent_selection(source)[1]) == len(option["key_patterns"])
        assert _not_sent(caplog) == []
        assert count_metric.call_args_list == []
    else:
        assert _sent_selection(source) == ([], [])
        assert [r.getMessage() for r in _not_sent(caplog)] == [NOT_SENT.format("too_many_values", URL_LIMIT)]
        assert [c.args for c in count_metric.call_args_list] == [
            (TELEMETRY_NAMESPACE.TRACERS, "flag_selection.dropped", 1, (("reason", "too_many_values"),))
        ]


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


def test_malformed_selection_after_none_reports_its_own_drop_without_warning(caplog, count_metric):
    with caplog.at_level(logging.WARNING, logger=SELECTION_LOGGER):
        _create_source(None)
        _create_source({"tags": "malformed"})
    assert len(_not_sent(caplog)) == 1
    assert SECOND_SELECTION_WARNING not in caplog.messages
    assert len(_metric_calls(count_metric, "flag_selection.dropped")) == 1


def test_no_selection_after_a_dropped_one_does_not_warn(caplog):
    with caplog.at_level(logging.WARNING, logger=SELECTION_LOGGER):
        _create_source({"tags": [f"t{i}" for i in range(32)]})
        _create_source(None)
    assert len(_not_sent(caplog)) == 1
    assert SECOND_SELECTION_WARNING not in caplog.messages


def test_dropped_selection_after_a_sent_one_reports_its_own_drop_and_warns(caplog):
    with caplog.at_level(logging.WARNING, logger=SELECTION_LOGGER):
        _create_source({"tags": ["a"]})
        _create_source({"tags": [f"t{i}" for i in range(32)]})
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
    _flag_selection.recording_loaded_selection(lambda _: None, a, lambda: None)({})
    claim_process_selection(b)

    def reject(_):
        raise ValueError("rejected")

    with pytest.raises(ValueError):
        _flag_selection.recording_loaded_selection(reject, b, lambda: None)({})
    assert _flag_selection.snapshot_from_other_selection() is True


def test_first_apply_under_a_new_selection_notifies_again_once_it_is_recorded():
    a = canonicalize_flag_selection({"tags": ["a"]})
    b = canonicalize_flag_selection({"tags": ["b"]})
    _flag_selection.recording_loaded_selection(lambda _: None, a, lambda: None)({})
    claim_process_selection(b)
    events = []

    def apply(_):
        events.append(("applied", _flag_selection.snapshot_from_other_selection()))

    def notify():
        events.append(("notified", _flag_selection.snapshot_from_other_selection()))

    apply_b = _flag_selection.recording_loaded_selection(apply, b, notify)
    apply_b({})
    apply_b({})
    assert events == [("applied", True), ("notified", False), ("applied", False)]


def test_missing_api_key_takes_no_claim():
    with override_global_config({"_dd_api_key": None, "_dd_site": "datadoghq.com", "env": "prod"}):
        with pytest.raises(MissingApiKeyError):
            create_agentless_source(_config(), lambda _: None, canonicalize_flag_selection({"tags": ["a"]}))
    assert claim_process_selection(canonicalize_flag_selection({"tags": ["z"]})).tags == ("z",)


def test_invalid_base_url_takes_no_claim():
    with pytest.raises(ValueError):
        _create_source({"tags": ["a"]}, base_url="ftp://flags.example")
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
