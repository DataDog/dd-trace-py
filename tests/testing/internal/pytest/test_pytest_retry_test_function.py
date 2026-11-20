"""Regression tests for plugins that wrap the test function, like pytest-mpl, under our retries.

pytest-mpl replaces item.obj in its pytest_runtest_call hookwrapper with a wrapper that stores the figure the test
returns and itself returns None. Our retries call runtestprotocol() again on the same item, so unless every attempt
starts from the test function the item had before its first attempt, each retry wraps the previous attempt's wrapper
and the plugin sees None instead of the returned value.

The conftest below reproduces that pattern without depending on pytest-mpl, and records what the plugin saw on each
attempt.
"""

from __future__ import annotations

import json
import pathlib
import typing as t
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


_TEST_REF = TestRef(SuiteRef(ModuleRef(""), "test_foo.py"), "test_returns_value")
_KNOWN_TEST_REF = TestRef(SuiteRef(ModuleRef(""), "test_foo.py"), "test_known")


class TestRetriesKeepTestFunction:
    @pytest.fixture(autouse=True)
    def _stable_retry_durations(self):
        # EFD budgets are duration-based: keep the test in the fast bucket (10 retries).
        with (
            patch("ddtrace.testing.internal.test_data.TestRun.seconds_so_far", return_value=0),
            patch("ddtrace.testing.internal.test_data.Test.seconds_so_far", return_value=0),
        ):
            yield

    @pytest.mark.parametrize(
        "api_client_settings,fail_first_attempt,expected_seen",
        [
            pytest.param(
                dict(efd_enabled=True, known_tests_enabled=True, known_tests={_KNOWN_TEST_REF}),
                False,
                ["value"] * 11,
                id="early_flake_detection",
            ),
            pytest.param(
                dict(auto_retries_enabled=True, known_tests_enabled=True, known_tests={_TEST_REF}),
                True,
                ["raised", "value"],
                id="auto_test_retries",
            ),
            pytest.param(
                dict(
                    test_management_enabled=True,
                    known_tests_enabled=True,
                    known_tests={_TEST_REF},
                    test_management_properties={_TEST_REF: TestProperties(attempt_to_fix=True)},
                ),
                False,
                ["value"] * 21,
                id="attempt_to_fix",
            ),
        ],
    )
    def test_each_attempt_wraps_the_original_test_function(
        self,
        pytester: Pytester,
        tmp_path: pathlib.Path,
        api_client_settings: dict[str, t.Any],
        fail_first_attempt: bool,
        expected_seen: list[str],
    ) -> None:
        seen_file = tmp_path / "seen.jsonl"
        pytester.makeconftest(
            f"""
            import json
            import pathlib

            import pytest

            _seen_file = pathlib.Path(r"{seen_file}")
            _returned = {{}}


            @pytest.hookimpl(hookwrapper=True)
            def pytest_runtest_call(item):
                test_function = item.obj

                def wrapper(*args, **kwargs):
                    _returned[item.nodeid] = test_function(*args, **kwargs)

                item.obj = wrapper
                outcome = yield
                seen = "raised" if outcome.excinfo else _returned.pop(item.nodeid, "missing")
                with _seen_file.open("a") as f:
                    f.write(json.dumps(seen) + "\\n")
            """
        )
        pytester.makepyfile(
            test_foo=f"""
            _attempts = []


            def test_returns_value():
                _attempts.append(None)
                if {fail_first_attempt} and len(_attempts) == 1:
                    raise RuntimeError("first attempt fails")
                return "value"
            """
        )

        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(**api_client_settings),
            ),
            setup_standard_mocks(),
        ):
            result = pytester.inline_run("--ddtrace")

        assert result.ret == 0
        seen = [json.loads(line) for line in seen_file.read_text().splitlines()]
        assert seen == expected_seen

    @pytest.mark.parametrize(
        "api_client_settings,expected_seen",
        [
            pytest.param(dict(), ["open"] * 2, id="no_retries"),
            pytest.param(
                dict(efd_enabled=True, known_tests_enabled=True, known_tests={_KNOWN_TEST_REF}),
                # The first item runs 1 + 10 attempts; the second item's test has already been retried.
                ["open"] * 12,
                id="early_flake_detection",
            ),
        ],
    )
    def test_items_collected_twice_run_on_their_own_instance(
        self,
        pytester: Pytester,
        tmp_path: pathlib.Path,
        api_client_settings: dict[str, t.Any],
        expected_seen: list[str],
    ) -> None:
        """Two items collected from one class test share a node ID; each must run on the instance its setup prepared."""
        seen_file = tmp_path / "seen.jsonl"
        pytester.makepyfile(
            test_foo=f"""
            import json
            import pathlib

            _seen_file = pathlib.Path(r"{seen_file}")


            class TestResource:
                def setup_method(self, method):
                    self.resource = "open"

                def teardown_method(self, method):
                    self.resource = "closed"

                def test_uses_resource(self):
                    with _seen_file.open("a") as f:
                        f.write(json.dumps(self.resource) + "\\n")
                    assert self.resource == "open"
            """
        )

        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(**api_client_settings),
            ),
            setup_standard_mocks(),
        ):
            result = pytester.inline_run("--ddtrace", "--keep-duplicates", "test_foo.py", "test_foo.py")

        assert result.ret == 0
        seen = [json.loads(line) for line in seen_file.read_text().splitlines()]
        assert seen == expected_seen

    def test_unittest_attempts_run_on_the_instance_their_set_up_prepared(self, pytester: Pytester) -> None:
        """pytest creates a new TestCase instance for every run of a unittest item; retries must not undo that."""
        pytester.makepyfile(
            test_foo="""
            import unittest


            class TestResource(unittest.TestCase):
                def setUp(self):
                    self.resource = "open"

                def tearDown(self):
                    self.resource = "closed"

                def test_uses_resource(self):
                    assert self.resource == "open"
            """
        )

        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(
                    efd_enabled=True, known_tests_enabled=True, known_tests={_KNOWN_TEST_REF}
                ),
            ),
            setup_standard_mocks(),
        ):
            with EventCapture.capture() as event_capture:
                result = pytester.inline_run("--ddtrace")

        assert result.ret == 0
        statuses = [event["content"]["meta"]["test.status"] for event in event_capture.events_by_type("test")]
        assert statuses == ["pass"] * 11
