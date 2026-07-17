import logging

import pytest

from ddtrace.internal.test_visibility.coverage_report_utils import _get_code_coverage_flags
from ddtrace.internal.test_visibility.coverage_report_utils import create_coverage_report_event


@pytest.fixture(autouse=True)
def reset_code_coverage_flags_cache():
    # AIDEV-NOTE: EFD/ATR retries rerun test calls without rerunning function fixtures, so tests
    # that assert warning counts also clear the cache inside their test bodies.
    _get_code_coverage_flags.cache_clear()
    yield
    _get_code_coverage_flags.cache_clear()


@pytest.mark.parametrize("raw_flags", [None, "", "   ", ", , ,"])
def test_coverage_report_event_omits_empty_flags(monkeypatch, caplog, raw_flags):
    if raw_flags is None:
        monkeypatch.delenv("DD_CODE_COVERAGE_FLAGS", raising=False)
    else:
        monkeypatch.setenv("DD_CODE_COVERAGE_FLAGS", raw_flags)

    event = create_coverage_report_event("lcov")

    assert "report.flags" not in event
    assert not caplog.records


def test_coverage_report_event_normalizes_flags(monkeypatch):
    monkeypatch.setenv("DD_CODE_COVERAGE_FLAGS", " type:unit-tests, ,jvm-21,type:unit-tests ")

    event = create_coverage_report_event("lcov")

    assert event["report.flags"] == ["type:unit-tests", "jvm-21", "type:unit-tests"]


def test_coverage_report_event_accepts_maximum_flags(monkeypatch):
    flags = [f"flag-{index}" for index in range(32)]
    monkeypatch.setenv("DD_CODE_COVERAGE_FLAGS", ",".join(flags))

    event = create_coverage_report_event("lcov")

    assert event["report.flags"] == flags


def test_coverage_report_event_omits_too_many_flags(monkeypatch, caplog):
    _get_code_coverage_flags.cache_clear()
    monkeypatch.setenv("DD_CODE_COVERAGE_FLAGS", ",".join(f"flag-{index}" for index in range(33)))

    with caplog.at_level(logging.WARNING):
        event = create_coverage_report_event("lcov")

    assert event["type"] == "coverage_report"
    assert event["format"] == "lcov"
    assert "timestamp" in event
    assert "report.flags" not in event
    assert caplog.messages == [
        "Code coverage report flags will be omitted because 33 flags were provided, exceeding the maximum of 32"
    ]


def test_coverage_report_flags_are_snapshotted_once(monkeypatch, caplog):
    _get_code_coverage_flags.cache_clear()
    monkeypatch.setenv("DD_CODE_COVERAGE_FLAGS", ",".join(f"flag-{index}" for index in range(33)))

    with caplog.at_level(logging.WARNING):
        first_event = create_coverage_report_event("lcov")
        monkeypatch.setenv("DD_CODE_COVERAGE_FLAGS", "later")
        second_event = create_coverage_report_event("cobertura")

    assert "report.flags" not in first_event
    assert "report.flags" not in second_event
    assert len(caplog.records) == 1


def test_coverage_report_flags_snapshot_is_reused(monkeypatch):
    monkeypatch.setenv("DD_CODE_COVERAGE_FLAGS", "first,second")

    first_event = create_coverage_report_event("lcov")
    monkeypatch.delenv("DD_CODE_COVERAGE_FLAGS")
    second_event = create_coverage_report_event("cobertura")

    assert first_event["report.flags"] == ["first", "second"]
    assert second_event["report.flags"] == ["first", "second"]


def test_coverage_report_flags_do_not_change_tag_filtering(monkeypatch):
    monkeypatch.setenv("DD_CODE_COVERAGE_FLAGS", "type:unit-tests")

    event = create_coverage_report_event(
        "lcov",
        tags={
            "git.branch": "main",
            "ci.pipeline.id": "123",
            "pr.number": "456",
            "service": "unrelated-service",
            "git.commit.sha": "",
        },
    )

    assert event["report.flags"] == ["type:unit-tests"]
    assert event["git.branch"] == "main"
    assert event["ci.pipeline.id"] == "123"
    assert event["pr.number"] == "456"
    assert "service" not in event
    assert "git.commit.sha" not in event
