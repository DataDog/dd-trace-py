from __future__ import annotations

from unittest.mock import patch

from _pytest.pytester import Pytester
import pytest

from ddtrace.testing.internal.settings_data import TestProperties
from ddtrace.testing.internal.test_data import ModuleRef
from ddtrace.testing.internal.test_data import SuiteRef
from ddtrace.testing.internal.test_data import TestRef
from tests.testing.mocks import EventCapture
from tests.testing.mocks import mock_api_client_settings
from tests.testing.mocks import setup_standard_mocks


@pytest.mark.parametrize("use_cli", [False, True])
@pytest.mark.parametrize("recovers", [False, True])
def test_excluded_quarantined_native_retries(pytester: Pytester, use_cli: bool, recovers: bool) -> None:
    pytest.importorskip("pytest_rerunfailures")
    marker = "" if use_cli else "@pytest.mark.flaky(reruns=1)"
    pytester.makepyfile(
        test_foo=f"""
        from pathlib import Path
        import pytest
        count = 0
        {marker}
        @pytest.mark.dd_no_auto_retry
        def test_quarantined():
            global count
            count += 1
            Path(__file__).with_suffix(".count").write_text(str(count))
            assert {recovers} and count == 2
        """
    )
    ref = TestRef(SuiteRef(ModuleRef(""), "test_foo.py"), "test_quarantined")
    with (
        patch(
            "ddtrace.testing.internal.session_manager.APIClient",
            return_value=mock_api_client_settings(
                auto_retries_enabled=True,
                test_management_enabled=True,
                known_tests_enabled=True,
                known_tests={ref},
                test_management_properties={ref: TestProperties(quarantined=True, attempt_to_fix=True)},
            ),
        ),
        setup_standard_mocks(),
        EventCapture.capture() as capture,
    ):
        args = ["--reruns", "1"] if use_cli else []
        result = pytester.inline_run("--ddtrace", "-p", "rerunfailures", "-q", *args)

    assert (pytester.path / "test_foo.count").read_text() == "2"
    assert result.ret == 0
    [event] = capture.events_by_test_name("test_quarantined")
    meta = event["content"]["meta"]
    assert meta["test.status"] == ("pass" if recovers else "fail")
    assert meta["test.test_management.no_auto_retry"] == "true"
    assert meta["test.test_management.is_quarantined"] == "true"
    assert meta["test.test_management.is_attempt_to_fix"] == "true"
    assert "test.retry_reason" not in meta
