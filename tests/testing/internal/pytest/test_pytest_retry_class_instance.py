"""Regression tests for the class instance a test runs on when our retries run it again.

pytest makes one instance of a test's class for each test item and keeps it on the item, since it runs each item once.
Our retries run the same item again. What every test here checks is that each attempt's body, its fixtures and its
setup_method see one instance of the class, and that no earlier attempt touched that instance: otherwise what an
attempt leaves on ``self`` reaches the next attempt, and through state the class's tests share, the tests that follow.
"""

from __future__ import annotations

import pathlib
import textwrap
import typing as t
from unittest.mock import patch

from _pytest.pytester import Pytester
import pytest

from ddtrace.internal.utils.version import parse_version
from ddtrace.testing.internal.test_data import ModuleRef
from ddtrace.testing.internal.test_data import SuiteRef
from ddtrace.testing.internal.test_data import TestRef
from tests.testing.internal.pytest.utils import assert_stats
from tests.testing.mocks import EventCapture
from tests.testing.mocks import mock_api_client_settings
from tests.testing.mocks import setup_standard_mocks


_KNOWN_TEST_REF = TestRef(SuiteRef(ModuleRef(""), "test_foo.py"), "TestListeners::test_known")

_EFD_SETTINGS: dict[str, t.Any] = dict(efd_enabled=True, known_tests_enabled=True, known_tests={_KNOWN_TEST_REF})
_ATR_SETTINGS: dict[str, t.Any] = dict(auto_retries_enabled=True)

# Each attempt's setup writes whether it runs on an instance an earlier attempt already set up, and the test body checks
# that it runs on the instance its setup prepared. The test passes once more than failing_attempts attempts have run.
_XUNIT_TEST_CLASS = """
import pathlib

_seen_file = pathlib.Path(r"{seen_file}")
_attempts = []


class TestResource:
    def setup_method(self, method):
        with _seen_file.open("a") as f:
            f.write("reused\\n" if hasattr(self, "resource") else "new\\n")
        self.resource = "open"

    def teardown_method(self, method):
        self.resource = "closed"

    def test_resource(self):
        assert self.resource == "open"
        _attempts.append(None)
        assert len(_attempts) > {failing_attempts}
"""

_UNITTEST_TEST_CLASS = """
import pathlib
import unittest

_seen_file = pathlib.Path(r"{seen_file}")
_attempts = []


class TestResource(unittest.TestCase):
    def setUp(self):
        with _seen_file.open("a") as f:
            f.write("reused\\n" if hasattr(self, "resource") else "new\\n")
        self.resource = "open"

    def tearDown(self):
        self.resource = "closed"

    def test_resource(self):
        assert self.resource == "open"
        _attempts.append(None)
        assert len(_attempts) > {failing_attempts}
"""


# The xunit class with its attributes in __slots__: in the class's own, so its instances have no __dict__, or in a
# base's, which leaves the class's name-mangled attribute in the instance's __dict__.
_SLOTS_TEST_CLASS = """
import pathlib

_seen_file = pathlib.Path(r"{seen_file}")


class _Resource:
    {base_slots}


class TestResource(_Resource):
    {class_slots}

    def setup_method(self, method):
        reused = hasattr(self, "resource") or hasattr(self, "_TestResource__marker")
        with _seen_file.open("a") as f:
            f.write("reused\\n" if reused else "new\\n")
        self.resource = "open"
        self.__marker = True

    def teardown_method(self, method):
        self.resource = "closed"

    def test_resource(self):
        assert self.resource == "open"
"""


class TestRetriesClassInstance:
    @pytest.fixture(autouse=True)
    def _stable_retry_durations(self):
        # EFD budgets are duration-based: keep the tests in the fast bucket (10 retries).
        with (
            patch("ddtrace.testing.internal.test_data.TestRun.seconds_so_far", return_value=0),
            patch("ddtrace.testing.internal.test_data.Test.seconds_so_far", return_value=0),
        ):
            yield

    @pytest.mark.parametrize(
        "added_listeners",
        [
            pytest.param(
                """
                @functools.cached_property
                def added_listeners(self):
                    return []
                """,
                id="instance_attribute",
            ),
            pytest.param(
                """
                @property
                def added_listeners(self):
                    return _added_listeners.setdefault(self, [])
                """,
                id="keyed_by_instance",
            ),
        ],
    )
    def test_retries_leave_no_state_to_the_next_tests_of_the_class(
        self, pytester: Pytester, added_listeners: str
    ) -> None:
        """SQLAlchemy's RemovesEvents fixture: a test adds listeners to shared state through self, which remembers them
        in an attribute made on first use (or here also in a dictionary keyed by the instance), and a function-scoped
        fixture removes them after each test.

        Run on the first attempt's instance, an Early Flake Detection retry removes that attempt's listener again,
        errors in its teardown and leaves its own listener to the class's next test. What setup_class sets up stays
        shared by the class's tests and their attempts, as without retries.
        """
        pytester.makepyfile(
            test_foo=f"""
            import functools
            import weakref

            import pytest

            _listeners = []
            _added_listeners = weakref.WeakKeyDictionary()


            class TestListeners:
                class_setups = 0

                @classmethod
                def setup_class(cls):
                    cls.class_setups += 1
{textwrap.indent(textwrap.dedent(added_listeners), " " * 16)}

                def listen(self, listener):
                    self.added_listeners.append(listener)
                    _listeners.append(listener)

                @pytest.fixture(autouse=True)
                def remove_listeners(self):
                    yield
                    for listener in self.added_listeners:
                        _listeners.remove(listener)

                def test_new(self):
                    self.listen(lambda: None)
                    assert len(_listeners) == 1

                def test_known(self):
                    assert _listeners == []
                    assert type(self).class_setups == 1
            """
        )

        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(**_EFD_SETTINGS),
            ),
            setup_standard_mocks(),
        ):
            with EventCapture.capture() as event_capture:
                result = pytester.inline_run("--ddtrace", "-p", "no:randomly")

        assert result.ret == 0
        assert_stats(result, passed=2)
        new_test_statuses = [
            event["content"]["meta"]["test.status"]
            for event in event_capture.events_by_test_name("TestListeners::test_new")
        ]
        assert new_test_statuses == ["pass"] * 11

    @pytest.mark.parametrize(
        "test_class,api_client_settings,out_of_session_retries,failing_attempts,expected_ret,expected_attempts",
        [
            pytest.param(_XUNIT_TEST_CLASS, _EFD_SETTINGS, False, 0, 0, 11, id="early_flake_detection"),
            pytest.param(_XUNIT_TEST_CLASS, _ATR_SETTINGS, False, 1, 0, 2, id="auto_test_retries"),
            # 1 attempt + 5 Auto Test Retries, all failing, then 1 out-of-session retry.
            pytest.param(_XUNIT_TEST_CLASS, _ATR_SETTINGS, True, 7, 1, 7, id="out_of_session"),
            # pytest makes a new TestCase for each run of a unittest item; retries must keep it that way.
            pytest.param(_UNITTEST_TEST_CLASS, _EFD_SETTINGS, False, 0, 0, 11, id="unittest_early_flake_detection"),
        ],
    )
    def test_each_attempt_starts_without_earlier_attempts_state(
        self,
        pytester: Pytester,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        test_class: str,
        api_client_settings: dict[str, t.Any],
        out_of_session_retries: bool,
        failing_attempts: int,
        expected_ret: int,
        expected_attempts: int,
    ) -> None:
        seen_file = tmp_path / "seen.txt"
        pytester.makepyfile(test_foo=test_class.format(seen_file=seen_file, failing_attempts=failing_attempts))
        if out_of_session_retries:
            monkeypatch.setenv("_DD_CIVISIBILITY_OUT_OF_SESSION_RETRIES_ENABLED", "1")

        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(**api_client_settings),
            ),
            setup_standard_mocks(),
        ):
            result = pytester.inline_run("--ddtrace", "-p", "no:randomly")

        assert result.ret == expected_ret
        assert seen_file.read_text().splitlines() == ["new"] * expected_attempts

    @pytest.mark.skipif(
        parse_version(pytest.__version__) < (7, 0, 0),
        reason="pytest 6 looks the test up by name on the class in every setup, so it can't run such an item at all",
    )
    @pytest.mark.parametrize(
        "callobj",
        [
            pytest.param("generated_test", id="function"),
            pytest.param("collector.obj().check", id="method_under_another_name"),
            pytest.param("collector.module.Derived().check_derived", id="method_of_a_subclass"),
        ],
    )
    def test_retries_keep_a_test_function_the_collector_gave_the_item(self, pytester: Pytester, callobj: str) -> None:
        """A collector can give an item of a class a test function that pytest can't look up on the class by the
        item's name: a plain function, a method of the class under another name, or a subclass's method. Retries must
        keep running that function, and a method of the class must start each attempt without the earlier attempts'
        state; the subclass's method, which the class can't make an instance for, keeps its own.
        """
        pytester.makeconftest(
            f"""
            import pytest


            def generated_test():
                pass


            @pytest.hookimpl(hookwrapper=True)
            def pytest_pycollect_makeitem(collector, name, obj):
                outcome = yield
                if name == "test_placeholder":
                    item = pytest.Function.from_parent(collector, name="test_generated", callobj={callobj})
                    outcome.force_result([item])
            """
        )
        pytester.makepyfile(
            test_foo="""
            class TestGenerated:
                def check(self):
                    assert not hasattr(self, "checked"), "this attempt runs on an earlier attempt's instance"
                    self.checked = True

                def test_placeholder(self):
                    pass


            class Derived(TestGenerated):
                def check_derived(self):
                    assert type(self) is Derived, "the subclass's method runs on an instance of another class"
            """
        )

        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(**_EFD_SETTINGS),
            ),
            setup_standard_mocks(),
        ):
            with EventCapture.capture() as event_capture:
                result = pytester.inline_run("--ddtrace", "-p", "no:randomly")

        assert result.ret == 0
        statuses = [
            event["content"]["meta"]["test.status"]
            for event in event_capture.events_by_test_name("TestGenerated::test_generated")
        ]
        assert statuses == ["pass"] * 11

    @pytest.mark.parametrize(
        "base_slots,class_slots",
        [
            pytest.param("__slots__ = ()", '__slots__ = ("resource", "__marker")', id="slots_only"),
            pytest.param('__slots__ = ("resource",)', "pass", id="slots_in_a_base"),
        ],
    )
    def test_attempts_start_without_earlier_attempts_slot_values(
        self, pytester: Pytester, tmp_path: pathlib.Path, base_slots: str, class_slots: str
    ) -> None:
        seen_file = tmp_path / "seen.txt"
        pytester.makepyfile(
            test_foo=_SLOTS_TEST_CLASS.format(seen_file=seen_file, base_slots=base_slots, class_slots=class_slots)
        )

        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(**_EFD_SETTINGS),
            ),
            setup_standard_mocks(),
        ):
            result = pytester.inline_run("--ddtrace", "-p", "no:randomly")

        assert result.ret == 0
        assert seen_file.read_text().splitlines() == ["new"] * 11

    def test_fixtures_set_the_instance_up_again_on_every_attempt(self, pytester: Pytester) -> None:
        """The attributes a function-scoped fixture sets on request.instance are there on every attempt."""
        pytester.makepyfile(
            test_foo="""
            import pytest


            @pytest.fixture(autouse=True)
            def resource(request):
                request.instance.resource = "open"
                yield
                request.instance.resource = "closed"


            class TestResource:
                def test_resource(self):
                    assert self.resource == "open"
            """
        )

        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(**_EFD_SETTINGS),
            ),
            setup_standard_mocks(),
        ):
            with EventCapture.capture() as event_capture:
                result = pytester.inline_run("--ddtrace", "-p", "no:randomly")

        assert result.ret == 0
        statuses = [
            event["content"]["meta"]["test.status"]
            for event in event_capture.events_by_test_name("TestResource::test_resource")
        ]
        assert statuses == ["pass"] * 11

    @pytest.mark.skipif(
        parse_version(pytest.__version__) < (7, 0, 0),
        reason="pytest 6 makes a new instance in every setup, so retries leave its items alone",
    )
    @pytest.mark.parametrize(
        "test_method",
        [
            pytest.param("def test_resource(self):\n    pass", id="method"),
            pytest.param("@staticmethod\ndef test_resource():\n    pass", id="staticmethod"),
            pytest.param("@classmethod\ndef test_resource(cls):\n    pass", id="classmethod"),
        ],
    )
    def test_new_instance_leaves_a_wrapper_in_the_test_method_alone(self, pytester: Pytester, test_method: str) -> None:
        """A plugin can wrap item.obj once and leave the wrapper there for the item's later runs. Making the new
        instance for a retry keeps that wrapper, and the instance it runs on, as they are.
        """
        from ddtrace.testing.internal.pytest.plugin import _use_new_test_class_instance

        item = pytester.getitem(
            "class TestResource:\n" + textwrap.indent(test_method, "    ") + "\n",
            funcname="test_resource",
        )
        _use_new_test_class_instance(item)  # Before the first attempt.
        test_function = item.obj

        def wrapper(*args, **kwargs):
            return test_function(*args, **kwargs)

        item.obj = wrapper
        instance = item.instance
        item._request = False  # What runtestprotocol() leaves after a run.
        _use_new_test_class_instance(item)  # Before the retry.

        assert item.obj is wrapper
        assert item.instance is instance

    @pytest.mark.skipif(
        parse_version(pytest.__version__) < (8, 2, 0),
        reason="before pytest 8.2 the fixtures of a staticmethod or classmethod test use one instance for every test",
    )
    def test_fixtures_of_staticmethod_and_classmethod_tests_get_a_new_instance(self, pytester: Pytester) -> None:
        pytester.makepyfile(
            test_foo="""
            import pytest


            class TestResource:
                @pytest.fixture(autouse=True)
                def resource(self):
                    assert not hasattr(self, "state"), "the fixture runs on an earlier attempt's instance"
                    self.state = "open"

                @staticmethod
                def test_static():
                    pass

                @classmethod
                def test_class(cls):
                    pass
            """
        )

        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(**_EFD_SETTINGS),
            ),
            setup_standard_mocks(),
        ):
            with EventCapture.capture() as event_capture:
                result = pytester.inline_run("--ddtrace", "-p", "no:randomly")

        assert result.ret == 0
        for test_name in ("TestResource::test_static", "TestResource::test_class"):
            statuses = [
                event["content"]["meta"]["test.status"] for event in event_capture.events_by_test_name(test_name)
            ]
            assert statuses == ["pass"] * 11, test_name

    def test_each_attempt_gets_the_instance_the_class_makes(self, pytester: Pytester) -> None:
        """A metaclass can set attributes up when the class makes an instance; each attempt must get them again."""
        pytester.makepyfile(
            test_foo="""
            class _Prepared(type):
                def __call__(cls, *args, **kwargs):
                    instance = super().__call__(*args, **kwargs)
                    instance.resource = "ready"
                    return instance


            class TestResource(metaclass=_Prepared):
                def test_resource(self):
                    assert self.resource == "ready"
                    self.resource = "used"
            """
        )

        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(**_EFD_SETTINGS),
            ),
            setup_standard_mocks(),
        ):
            with EventCapture.capture() as event_capture:
                result = pytester.inline_run("--ddtrace", "-p", "no:randomly")

        assert result.ret == 0
        statuses = [
            event["content"]["meta"]["test.status"]
            for event in event_capture.events_by_test_name("TestResource::test_resource")
        ]
        assert statuses == ["pass"] * 11

    @pytest.mark.skipif(
        parse_version(pytest.__version__) < (7, 0, 0), reason="pytest 6 doesn't collect classmethod tests"
    )
    def test_classmethod_tests_stay_bound_to_their_class(self, pytester: Pytester) -> None:
        """A metaclass's __instancecheck__ can take the class itself for one of its instances; a classmethod test must
        still get its class on every attempt.
        """
        pytester.makepyfile(
            test_foo="""
            class _Structural(type):
                def __instancecheck__(cls, candidate):
                    return hasattr(candidate, "test_resource")


            class TestResource(metaclass=_Structural):
                @classmethod
                def test_resource(cls):
                    assert isinstance(cls, type), "the classmethod got an instance"
            """
        )

        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(**_EFD_SETTINGS),
            ),
            setup_standard_mocks(),
        ):
            with EventCapture.capture() as event_capture:
                result = pytester.inline_run("--ddtrace", "-p", "no:randomly")

        assert result.ret == 0
        statuses = [
            event["content"]["meta"]["test.status"]
            for event in event_capture.events_by_test_name("TestResource::test_resource")
        ]
        assert statuses == ["pass"] * 11

    @pytest.mark.skipif(
        parse_version(pytest.__version__) < (7, 0, 0),
        reason="pytest 6 makes a new instance in every setup, so retries leave its instances alone",
    )
    @pytest.mark.parametrize(
        "raise_error",
        [
            pytest.param('raise RuntimeError("no new instance")', id="exception"),
            pytest.param('pytest.skip("no new instance")', id="pytest_skip"),
        ],
    )
    def test_retries_run_when_the_class_cant_make_a_new_instance(self, pytester: Pytester, raise_error: str) -> None:
        """If the class can't make a new instance for a retry, the retry runs on the current one, as without the
        change, also when making it calls pytest.skip(), whose exception isn't an Exception.
        """
        pytester.makeconftest(
            f"""
            import pytest


            def _no_new_instance():
                {raise_error}


            def pytest_collection_finish(session):
                for item in session.items:
                    item.parent.newinstance = _no_new_instance
            """
        )
        pytester.makepyfile(
            test_foo="""
            class TestResource:
                def test_resource(self):
                    pass
            """
        )

        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(**_EFD_SETTINGS),
            ),
            setup_standard_mocks(),
        ):
            with EventCapture.capture() as event_capture:
                result = pytester.inline_run("--ddtrace", "-p", "no:randomly")

        assert result.ret == 0
        statuses = [
            event["content"]["meta"]["test.status"]
            for event in event_capture.events_by_test_name("TestResource::test_resource")
        ]
        assert statuses == ["pass"] * 11

    @pytest.mark.skipif(
        parse_version(pytest.__version__) < (8, 2, 0),
        reason="before pytest 8.2 the instance is the test method's own, so the two can't differ",
    )
    def test_failed_new_instance_binds_the_test_method_to_the_fixtures_instance(self, pytester: Pytester) -> None:
        """After a retry got a new instance, a plugin can put back the first attempt's method before the next one. If
        the class then can't make an instance, the method is bound to the instance the fixtures use.
        """
        from ddtrace.testing.internal.pytest.plugin import _use_new_test_class_instance

        item = pytester.getitem(
            """
            class TestResource:
                def test_resource(self):
                    pass
            """,
            funcname="test_resource",
        )
        first_instance = item.instance
        fixtures_instance = type(first_instance)()
        item._instance = fixtures_instance
        item.obj = first_instance.test_resource
        item._request = False  # What runtestprotocol() leaves after a run.

        def no_new_instance():
            raise RuntimeError("no new instance")

        item.parent.newinstance = no_new_instance
        _use_new_test_class_instance(item)

        assert item.obj.__self__ is fixtures_instance
        assert item.instance is fixtures_instance
