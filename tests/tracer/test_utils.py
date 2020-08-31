from functools import partial
import mock
import os
import unittest
import warnings

import pytest

from ddtrace.utils import time
from ddtrace.utils.importlib import func_name
from ddtrace.utils.deprecation import deprecation, deprecated, format_message
from ddtrace.utils.formats import asbool, get_env, parse_tags_str


class TestUtils(unittest.TestCase):
    def test_asbool(self):
        # ensure the value is properly cast
        self.assertTrue(asbool("True"))
        self.assertTrue(asbool("true"))
        self.assertTrue(asbool("1"))
        self.assertFalse(asbool("False"))
        self.assertFalse(asbool("false"))
        self.assertFalse(asbool(None))
        self.assertFalse(asbool(""))
        self.assertTrue(asbool(True))
        self.assertFalse(asbool(False))

    def test_get_env(self):
        # ensure `get_env` returns a default value if environment variables
        # are not set
        value = get_env("django", "distributed_tracing")
        self.assertIsNone(value)
        value = get_env("django", "distributed_tracing", default=False)
        self.assertFalse(value)

    def test_get_env_long(self):
        os.environ["DD_SOME_VERY_LONG_TEST_KEY"] = "1"
        value = get_env("some", "very", "long", "test", "key", default="2")
        assert value == "1"

    def test_get_env_found(self):
        # ensure `get_env` returns a value if the environment variable is set
        os.environ["DD_REQUESTS_DISTRIBUTED_TRACING"] = "1"
        value = get_env("requests", "distributed_tracing")
        self.assertEqual(value, "1")

    def test_get_env_found_legacy(self):
        # ensure `get_env` returns a value if legacy environment variables
        # are used, raising a Deprecation warning
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            os.environ["DATADOG_REQUESTS_DISTRIBUTED_TRACING"] = "1"
            value = get_env("requests", "distributed_tracing")
            self.assertEqual(value, "1")
            self.assertEqual(len(w), 1)
            self.assertTrue(issubclass(w[-1].category, DeprecationWarning))
            self.assertTrue("Use `DD_` prefix instead" in str(w[-1].message))

    def test_get_env_key_priority(self):
        # ensure `get_env` use `DD_` with highest priority
        os.environ["DD_REQUESTS_DISTRIBUTED_TRACING"] = "highest"
        os.environ["DATADOG_REQUESTS_DISTRIBUTED_TRACING"] = "lowest"
        value = get_env("requests", "distributed_tracing")
        self.assertEqual(value, "highest")

    def test_deprecation_formatter(self):
        # ensure the formatter returns the proper message
        msg = format_message("deprecated_function", "use something else instead", "1.0.0",)
        expected = (
            "'deprecated_function' is deprecated and will be remove in future versions (1.0.0). "
            "use something else instead"
        )
        self.assertEqual(msg, expected)

    def test_deprecation(self):
        # ensure `deprecation` properly raise a DeprecationWarning
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            deprecation(name="fn", message="message", version="1.0.0")
            self.assertEqual(len(w), 1)
            self.assertTrue(issubclass(w[-1].category, DeprecationWarning))
            self.assertIn("message", str(w[-1].message))

    def test_deprecated_decorator(self):
        # ensure `deprecated` decorator properly raise a DeprecationWarning
        @deprecated("decorator", version="1.0.0")
        def fxn():
            pass

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            fxn()
            self.assertEqual(len(w), 1)
            self.assertTrue(issubclass(w[-1].category, DeprecationWarning))
            self.assertIn("decorator", str(w[-1].message))

    def test_parse_env_tags(self):
        tags = parse_tags_str("key:val")
        assert tags == dict(key="val")

        tags = parse_tags_str("key:val,key2:val2")
        assert tags == dict(key="val", key2="val2")

        tags = parse_tags_str("key:val,key2:val2,key3:1234.23")
        assert tags == dict(key="val", key2="val2", key3="1234.23")

        with mock.patch("ddtrace.utils.formats.log") as log:
            tags = parse_tags_str("key:,key3:val1,")
        assert tags == dict(key3="val1")
        assert log.error.call_count == 2

        with mock.patch("ddtrace.utils.formats.log") as log:
            tags = parse_tags_str("")
        assert tags == dict()
        assert log.error.call_count == 0

        with mock.patch("ddtrace.utils.formats.log") as log:
            tags = parse_tags_str(",")
        assert tags == dict()
        assert log.error.call_count == 2

        with mock.patch("ddtrace.utils.formats.log") as log:
            tags = parse_tags_str(":,:")
        assert tags == dict()
        assert log.error.call_count == 2

        with mock.patch("ddtrace.utils.formats.log") as log:
            tags = parse_tags_str("key,key2:val1")
        assert tags == dict(key2="val1")
        log.error.assert_called_once_with(
            "Malformed tag in tag pair '%s' from tag string '%s'.", "key", "key,key2:val1"
        )

        with mock.patch("ddtrace.utils.formats.log") as log:
            tags = parse_tags_str("key2:val1:")
        assert tags == dict()
        log.error.assert_called_once_with(
            "Malformed tag in tag pair '%s' from tag string '%s'.", "key2:val1:", "key2:val1:"
        )

        with mock.patch("ddtrace.utils.formats.log") as log:
            tags = parse_tags_str("key,key2,key3")
        assert tags == dict()
        log.error.assert_has_calls(
            [
                mock.call("Malformed tag in tag pair '%s' from tag string '%s'.", "key", "key,key2,key3"),
                mock.call("Malformed tag in tag pair '%s' from tag string '%s'.", "key2", "key,key2,key3"),
                mock.call("Malformed tag in tag pair '%s' from tag string '%s'.", "key3", "key,key2,key3"),
            ]
        )


def test_no_states():
    watch = time.StopWatch()
    with pytest.raises(RuntimeError):
        watch.stop()


def test_start_stop():
    watch = time.StopWatch()
    watch.start()
    watch.stop()


def test_start_stop_elapsed():
    watch = time.StopWatch()
    watch.start()
    watch.stop()
    e = watch.elapsed()
    assert e > 0
    watch.start()
    assert watch.elapsed() != e


def test_no_elapsed():
    watch = time.StopWatch()
    with pytest.raises(RuntimeError):
        watch.elapsed()


def test_elapsed():
    watch = time.StopWatch()
    watch.start()
    watch.stop()
    assert watch.elapsed() > 0


def test_context_manager():
    with time.StopWatch() as watch:
        pass
    assert watch.elapsed() > 0


class SomethingCallable(object):
    """
    A dummy class that implements __call__().
    """

    value = 42

    def __call__(self):
        return "something"

    def me(self):
        return self

    @staticmethod
    def add(a, b):
        return a + b

    @classmethod
    def answer(cls):
        return cls.value


def some_function():
    """
    A function doing nothing.
    """
    return "nothing"


def minus(a, b):
    return a - b


minus_two = partial(minus, b=2)  # partial funcs need special handling (no module)

# disabling flake8 test below, yes, declaring a func like this is bad, we know
plus_three = lambda x: x + 3  # noqa: E731


class TestContrib(object):
    """
    Ensure that contrib utility functions handles corner cases
    """

    def test_func_name(self):
        # check that func_name works on anything callable, not only funcs.
        assert "nothing" == some_function()
        assert "tests.tracer.test_utils.some_function" == func_name(some_function)

        f = SomethingCallable()
        assert "something" == f()
        assert "tests.tracer.test_utils.SomethingCallable" == func_name(f)

        assert f == f.me()
        assert "tests.tracer.test_utils.me" == func_name(f.me)
        assert 3 == f.add(1, 2)
        assert "tests.tracer.test_utils.add" == func_name(f.add)
        assert 42 == f.answer()
        assert "tests.tracer.test_utils.answer" == func_name(f.answer)

        assert "tests.tracer.test_utils.minus" == func_name(minus)
        assert 5 == minus_two(7)
        assert "partial" == func_name(minus_two)
        assert 10 == plus_three(7)
        assert "tests.tracer.test_utils.<lambda>" == func_name(plus_three)
