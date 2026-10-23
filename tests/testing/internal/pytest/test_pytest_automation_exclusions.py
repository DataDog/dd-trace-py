from __future__ import annotations

from unittest.mock import patch

from _pytest.pytester import Pytester
import pytest

from ddtrace.testing import no_auto_retry
from ddtrace.testing import prevent_failure_suppression
from ddtrace.testing.internal.settings_data import TestProperties
from ddtrace.testing.internal.test_data import ModuleRef
from ddtrace.testing.internal.test_data import SuiteRef
from ddtrace.testing.internal.test_data import TestRef
from tests.testing.mocks import EventCapture
from tests.testing.mocks import mock_api_client_settings
from tests.testing.mocks import setup_standard_mocks


PREFIX = "test.test_management."


def _ref(name: str) -> TestRef:
    return TestRef(SuiteRef(ModuleRef(""), "test_foo.py"), name)


def test_decorators_preserve_callback() -> None:
    async def callback(value: int) -> int:
        return value

    assert no_auto_retry(prevent_failure_suppression(callback)) is callback


@pytest.mark.parametrize("passing", [False, True])
@pytest.mark.parametrize("protected,excluded", [(True, False), (False, True), (True, True), (False, False)])
def test_management_exclusions(pytester: Pytester, protected: bool, excluded: bool, passing: bool) -> None:
    decorators = ("@prevent_failure_suppression\n" if protected else "") + ("@no_auto_retry\n" if excluded else "")
    pytester.makepyfile(
        test_foo=(
            "from ddtrace.testing import prevent_failure_suppression, no_auto_retry\n"
            + decorators
            + f"def test_policy():\n    assert {passing}\n"
        )
    )
    props = TestProperties(disabled=True, quarantined=True, attempt_to_fix=True)
    with (
        patch(
            "ddtrace.testing.internal.session_manager.APIClient",
            return_value=mock_api_client_settings(
                auto_retries_enabled=True,
                test_management_enabled=True,
                known_tests_enabled=True,
                known_tests={_ref("test_policy")},
                test_management_properties={_ref("test_policy"): props},
            ),
        ),
        setup_standard_mocks(),
        EventCapture.capture() as capture,
    ):
        result = pytester.inline_run("--ddtrace", "-q")

    events = list(capture.events_by_test_name("test_policy"))
    assert len(events) == (21 if passing and not excluded else 1)
    for event in events:
        meta = event["content"]["meta"]
        for tag in ("is_test_disabled", "is_quarantined", "is_attempt_to_fix"):
            assert meta[PREFIX + tag] == "true"
        assert meta.get(PREFIX + "prevent_failure_suppression") == ("true" if protected else None)
        assert meta.get(PREFIX + "no_auto_retry") == ("true" if excluded else None)
        if excluded:
            assert "test.retry_reason" not in meta
            assert PREFIX + "attempt_to_fix_passed" not in meta
    if excluded:
        assert len(events) == 1
        expected_status = ("pass" if passing else "fail") if protected else "skip"
        assert events[0]["content"]["meta"]["test.status"] == expected_status
    expected_failure = not passing and (protected or not excluded)
    assert result.ret == (1 if expected_failure else 0)


@pytest.mark.parametrize("efd", [False, True])
def test_no_auto_retry_keeps_discovery_and_control_retries(pytester: Pytester, efd: bool) -> None:
    pytester.makepyfile(
        test_foo=f"""
        from ddtrace.testing import no_auto_retry
        @no_auto_retry
        def test_excluded():
            assert {efd}
        def test_control():
            assert {efd}
    """
    )
    known = {_ref("test_known")} if efd else {_ref("test_excluded"), _ref("test_control")}
    with (
        patch(
            "ddtrace.testing.internal.session_manager.APIClient",
            return_value=mock_api_client_settings(
                auto_retries_enabled=True,
                efd_enabled=efd,
                known_tests_enabled=True,
                known_tests=known,
            ),
        ),
        patch("ddtrace.testing.internal.test_data.TestRun.seconds_so_far", return_value=0),
        patch("ddtrace.testing.internal.test_data.Test.seconds_so_far", return_value=0),
        setup_standard_mocks(),
        EventCapture.capture() as capture,
    ):
        result = pytester.inline_run("--ddtrace", "-q")
    excluded_events = list(capture.events_by_test_name("test_excluded"))
    assert len(excluded_events) == 1
    meta = excluded_events[0]["content"]["meta"]
    assert meta[PREFIX + "no_auto_retry"] == "true"
    assert meta.get("test.is_new") == ("true" if efd else None)
    assert "test.is_retry" not in meta
    assert len(list(capture.events_by_test_name("test_control"))) == (11 if efd else 6)
    assert result.ret == (0 if efd else 1)


def test_parameterized_and_skipped_annotations(pytester: Pytester) -> None:
    pytester.makepyfile(
        test_foo="""
        import pytest
        from ddtrace.testing import no_auto_retry, prevent_failure_suppression
        @pytest.mark.parametrize("value", [1, 2])
        @no_auto_retry
        @prevent_failure_suppression
        def test_cases(value):
            assert False
        @pytest.mark.skip(reason="developer skip")
        @no_auto_retry
        @prevent_failure_suppression
        def test_skip():
            assert False
    """
    )
    with (
        patch(
            "ddtrace.testing.internal.session_manager.APIClient",
            return_value=mock_api_client_settings(auto_retries_enabled=True),
        ),
        setup_standard_mocks(),
        EventCapture.capture() as capture,
    ):
        result = pytester.inline_run("--ddtrace", "-q")
    assert result.ret == 1
    events = list(capture.events_by_type("test"))
    assert len(events) == 3
    for event in events:
        meta = event["content"]["meta"]
        assert meta[PREFIX + "no_auto_retry"] == "true"
        assert meta[PREFIX + "prevent_failure_suppression"] == "true"
        assert "test.is_retry" not in meta
    assert list(capture.events_by_test_name("test_skip"))[0]["content"]["meta"]["test.status"] == "skip"


@pytest.mark.parametrize("quarantined,disabled", [(True, False), (False, True)])
@pytest.mark.parametrize("protected", [True, False])
def test_failure_suppression_policy(pytester: Pytester, quarantined: bool, disabled: bool, protected: bool) -> None:
    decorator = "@prevent_failure_suppression\n" if protected else ""
    pytester.makepyfile(
        test_foo=(
            "from ddtrace.testing import prevent_failure_suppression\n"
            + decorator
            + "def test_policy():\n    assert False\n"
        )
    )
    with (
        patch(
            "ddtrace.testing.internal.session_manager.APIClient",
            return_value=mock_api_client_settings(
                test_management_enabled=True,
                known_tests_enabled=True,
                known_tests={_ref("test_policy")},
                test_management_properties={
                    _ref("test_policy"): TestProperties(quarantined=quarantined, disabled=disabled)
                },
            ),
        ),
        setup_standard_mocks(),
        EventCapture.capture() as capture,
    ):
        result = pytester.inline_run("--ddtrace", "-q")
    [event] = capture.events_by_test_name("test_policy")
    meta = event["content"]["meta"]
    assert meta[PREFIX + ("is_quarantined" if quarantined else "is_test_disabled")] == "true"
    assert meta["test.status"] == ("skip" if disabled and not protected else "fail")
    assert result.ret == (1 if protected else 0)


def test_protection_permits_atr_recovery(pytester: Pytester) -> None:
    pytester.makepyfile(
        test_foo="""
        from ddtrace.testing import prevent_failure_suppression
        count = 0
        @prevent_failure_suppression
        def test_recovery():
            global count
            count += 1
            assert count == 2
    """
    )
    with (
        patch(
            "ddtrace.testing.internal.session_manager.APIClient",
            return_value=mock_api_client_settings(
                auto_retries_enabled=True,
                test_management_enabled=True,
                known_tests_enabled=True,
                known_tests={_ref("test_recovery")},
                test_management_properties={_ref("test_recovery"): TestProperties(quarantined=True, disabled=True)},
            ),
        ),
        setup_standard_mocks(),
        EventCapture.capture() as capture,
    ):
        result = pytester.inline_run("--ddtrace", "-q")
    assert result.ret == 0
    events = list(capture.events_by_test_name("test_recovery"))
    assert len(events) == 2
    assert [event["content"]["meta"]["test.status"] for event in events] == ["fail", "pass"]
    for event in events:
        assert event["content"]["meta"][PREFIX + "prevent_failure_suppression"] == "true"
        assert event["content"]["meta"][PREFIX + "is_quarantined"] == "true"
        assert event["content"]["meta"][PREFIX + "is_test_disabled"] == "true"


def test_excluded_attempt_to_fix_keeps_quarantine(pytester: Pytester) -> None:
    pytester.makepyfile(
        test_foo="""
        from ddtrace.testing import no_auto_retry
        @no_auto_retry
        def test_quarantined():
            assert False
    """
    )
    with (
        patch(
            "ddtrace.testing.internal.session_manager.APIClient",
            return_value=mock_api_client_settings(
                auto_retries_enabled=True,
                test_management_enabled=True,
                known_tests_enabled=True,
                known_tests={_ref("test_quarantined")},
                test_management_properties={
                    _ref("test_quarantined"): TestProperties(quarantined=True, attempt_to_fix=True)
                },
            ),
        ),
        setup_standard_mocks(),
        EventCapture.capture() as capture,
    ):
        result = pytester.inline_run("--ddtrace", "-q")
    assert result.ret == 0
    [event] = capture.events_by_test_name("test_quarantined")
    meta = event["content"]["meta"]
    assert meta["test.status"] == "fail"
    assert meta[PREFIX + "is_quarantined"] == "true"
    assert meta[PREFIX + "is_attempt_to_fix"] == "true"
    assert PREFIX + "attempt_to_fix_passed" not in meta
    assert "test.retry_reason" not in meta


def test_exclusions_preserve_tia_selection(pytester: Pytester) -> None:
    pytester.makepyfile(
        test_foo="""
        from ddtrace.testing import no_auto_retry, prevent_failure_suppression
        @no_auto_retry
        @prevent_failure_suppression
        def test_skipped():
            assert False
    """
    )
    with (
        patch(
            "ddtrace.testing.internal.session_manager.APIClient",
            return_value=mock_api_client_settings(
                skipping_enabled=True,
                auto_retries_enabled=True,
                skippable_items={_ref("test_skipped").suite},
            ),
        ),
        setup_standard_mocks(),
        EventCapture.capture() as capture,
    ):
        result = pytester.inline_run("--ddtrace", "-q")
    assert result.ret == 0
    # Whole-file TIA skipping may happen before Python discovers annotations.
    assert not any(event["content"]["meta"]["test.status"] == "fail" for event in capture.events_by_type("test"))


def test_removing_annotations_restores_policy(pytester: Pytester) -> None:
    for annotated in (True, False):
        decorators = "@no_auto_retry\n@prevent_failure_suppression\n" if annotated else ""
        pytester.makepyfile(
            test_foo=(
                "from ddtrace.testing import no_auto_retry, prevent_failure_suppression\n"
                + decorators
                + "def test_policy():\n    assert False\n"
            )
        )
        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(
                    auto_retries_enabled=True,
                    test_management_enabled=True,
                    known_tests_enabled=True,
                    known_tests={_ref("test_policy")},
                    test_management_properties={_ref("test_policy"): TestProperties(quarantined=True)},
                ),
            ),
            setup_standard_mocks(),
            EventCapture.capture() as capture,
        ):
            result = pytester.inline_run("--ddtrace", "-q")
        assert result.ret == (1 if annotated else 0)
        events = list(capture.events_by_test_name("test_policy"))
        assert len(events) == (1 if annotated else 6)
        for event in events:
            meta = event["content"]["meta"]
            for annotation in ("no_auto_retry", "prevent_failure_suppression"):
                assert meta.get(PREFIX + annotation) == ("true" if annotated else None)


def test_excluded_attempt_to_fix_passing_test_runs_once(pytester: Pytester) -> None:
    pytester.makepyfile(
        test_foo="""
        from ddtrace.testing import no_auto_retry
        @no_auto_retry
        def test_excluded():
            pass
        def test_control():
            pass
    """
    )
    known = {_ref("test_excluded"), _ref("test_control")}
    client = mock_api_client_settings(
        test_management_enabled=True,
        known_tests_enabled=True,
        known_tests=known,
        test_management_properties={ref: TestProperties(attempt_to_fix=True) for ref in known},
    )
    client.get_settings.return_value.test_management.attempt_to_fix_retries = 2
    with (
        patch("ddtrace.testing.internal.session_manager.APIClient", return_value=client),
        setup_standard_mocks(),
        EventCapture.capture() as capture,
    ):
        result = pytester.inline_run("--ddtrace", "-q")
    assert result.ret == 0
    [excluded] = capture.events_by_test_name("test_excluded")
    meta = excluded["content"]["meta"]
    assert meta[PREFIX + "is_attempt_to_fix"] == "true"
    assert meta[PREFIX + "no_auto_retry"] == "true"
    assert "test.is_retry" not in meta
    assert PREFIX + "attempt_to_fix_passed" not in meta
    control = list(capture.events_by_test_name("test_control"))
    assert len(control) == 3
    assert control[-1]["content"]["meta"][PREFIX + "attempt_to_fix_passed"] == "true"
