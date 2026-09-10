from __future__ import annotations

from unittest.mock import patch

import pytest

from ddtrace.testing.internal.dynamic_atr_retries import DYNAMIC_ATR_BUCKETS_ENV
from ddtrace.testing.internal.dynamic_atr_retries import DYNAMIC_ATR_ENABLED_ENV
from ddtrace.testing.internal.dynamic_atr_retries import DynamicATRRetriesHandler
from ddtrace.testing.internal.dynamic_atr_retries import get_retries_buckets
from ddtrace.testing.internal.dynamic_atr_retries import is_dynamic_retries_enabled
from ddtrace.testing.internal.settings_data import AutoTestRetriesSettings
from ddtrace.testing.internal.settings_data import EarlyFlakeDetectionSettings
from ddtrace.testing.internal.settings_data import Settings
from ddtrace.testing.internal.test_data import Test
from ddtrace.testing.internal.test_data import TestModule
from ddtrace.testing.internal.test_data import TestSession
from ddtrace.testing.internal.test_data import TestStatus
from ddtrace.testing.internal.test_data import TestSuite


@pytest.fixture
def retry_settings() -> Settings:
    return Settings(
        auto_test_retries=AutoTestRetriesSettings(enabled=True),
        early_flake_detection=EarlyFlakeDetectionSettings(
            slow_test_retries_5s=10,
            slow_test_retries_10s=2,
            slow_test_retries_30s=3,
            slow_test_retries_5m=4,
        ),
    )


def _make_failing_test() -> Test:
    session = TestSession("session")
    module = TestModule("module", session)
    suite = TestSuite("suite", module)
    return Test("test", suite)


def _add_failed_run(test: Test):
    test_run = test.make_test_run()
    test_run.start()
    test_run.set_status(TestStatus.FAIL)
    test_run.finish()
    return test_run


@pytest.mark.parametrize(
    "enabled,expected",
    ((None, False), ("false", False), ("true", True), ("1", True)),
)
def test_dynamic_retries_enablement(enabled: str | None, expected: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    if enabled is None:
        monkeypatch.delenv(DYNAMIC_ATR_ENABLED_ENV, raising=False)
    else:
        monkeypatch.setenv(DYNAMIC_ATR_ENABLED_ENV, enabled)

    assert is_dynamic_retries_enabled() is expected


@pytest.mark.parametrize(
    "buckets,expected",
    (
        (None, None),
        ("10,4,1,1,1", (10, 4, 1, 1, 1)),
        ("10,4,1", None),
        ("10,4,0,1,1", None),
        ("21,4,1,1,1", None),
        ("invalid", None),
    ),
)
def test_retries_buckets_parsing(
    buckets: str | None, expected: tuple[int, int, int, int, int] | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    if buckets is None:
        monkeypatch.delenv(DYNAMIC_ATR_BUCKETS_ENV, raising=False)
    else:
        monkeypatch.setenv(DYNAMIC_ATR_BUCKETS_ENV, buckets)

    assert get_retries_buckets() == expected


@pytest.mark.parametrize(
    "initial_duration_seconds,expected_retries",
    ((1, 10), (6, 2), (31, 4), (301, 1)),
)
def test_dynamic_atr_uses_efd_retry_budgets(
    initial_duration_seconds: float,
    expected_retries: int,
    retry_settings: Settings,
) -> None:
    test = _make_failing_test()
    handler = DynamicATRRetriesHandler(retry_settings)
    initial_test_run = _add_failed_run(test)

    with patch.object(initial_test_run, "seconds_so_far", return_value=initial_duration_seconds):
        retries = 0
        while handler.should_retry(test):
            retries += 1
            _add_failed_run(test)

    assert retries == expected_retries


@pytest.mark.parametrize(
    "initial_duration_seconds,expected_retries",
    ((1, 4), (6, 1), (31, 1), (301, 1)),
)
def test_dynamic_atr_uses_custom_retry_budgets(
    initial_duration_seconds: float,
    expected_retries: int,
    retry_settings: Settings,
) -> None:
    test = _make_failing_test()
    handler = DynamicATRRetriesHandler(retry_settings, (4, 1, 1, 1, 1))
    initial_test_run = _add_failed_run(test)

    with patch.object(initial_test_run, "seconds_so_far", return_value=initial_duration_seconds):
        retries = 0
        while handler.should_retry(test):
            retries += 1
            _add_failed_run(test)

    assert retries == expected_retries


def test_dynamic_atr_classification_is_cached(retry_settings: Settings) -> None:
    test = _make_failing_test()
    handler = DynamicATRRetriesHandler(retry_settings)
    initial_test_run = _add_failed_run(test)

    with patch.object(initial_test_run, "seconds_so_far", return_value=1):
        assert handler.should_retry(test) is True
        assert handler.should_retry(test) is True

    assert handler._max_retries_for.cache_info().hits == 1


def test_dynamic_atr_ignores_normal_retry_count(retry_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    test = _make_failing_test()
    monkeypatch.setenv("DD_CIVISIBILITY_FLAKY_RETRY_COUNT", "0")
    handler = DynamicATRRetriesHandler(retry_settings)
    initial_test_run = _add_failed_run(test)

    with patch.object(initial_test_run, "seconds_so_far", return_value=1):
        assert handler.should_retry(test) is True
