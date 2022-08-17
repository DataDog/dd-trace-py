from functools import partial
import sys
import typing
import unittest

import mock
import pytest

from ddtrace.internal.utils import ArgumentError
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils import set_argument_value
from ddtrace.internal.utils import time
from ddtrace.internal.utils.cache import cached
from ddtrace.internal.utils.cache import cachedmethod
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.formats import parse_tags_str
from ddtrace.internal.utils.importlib import func_name
from ddtrace.internal.utils.version import parse_version


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


_LOG_ERROR_MALFORMED_TAG = "Malformed tag in tag pair '%s' from tag string '%s'."
_LOG_ERROR_MALFORMED_TAG_STRING = "Malformed tag string with tags not separated by comma or space '%s'."
_LOG_ERROR_FAIL_SEPARATOR = (
    "Failed to find separator for tag string: '%s'.\n"
    "Tag strings must be comma or space separated:\n"
    "  key1:value1,key2:value2\n"
    "  key1:value1 key2:value2"
)


@pytest.mark.parametrize(
    "tag_str,expected_tags,error_calls",
    [
        ("", dict(), None),
        ("key:val", dict(key="val"), None),
        ("key:val,key2:val2", dict(key="val", key2="val2"), None),
        ("key:val,key2:val2,key3:1234.23", dict(key="val", key2="val2", key3="1234.23"), None),
        ("key:val key2:val2 key3:1234.23", dict(key="val", key2="val2", key3="1234.23"), None),
        ("key: val", dict(key=" val"), None),
        ("key key: val", {"key key": " val"}, None),
        ("key: val,key2:val2", dict(key=" val", key2="val2"), None),
        (" key: val,key2:val2", {" key": " val", "key2": "val2"}, None),
        ("key key2:val1", {"key key2": "val1"}, None),
        (
            "key:val key2:val:2",
            dict(),
            [mock.call(_LOG_ERROR_MALFORMED_TAG_STRING, "key:val key2:val:2")],
        ),
        (
            "key:val,key2:val2 key3:1234.23",
            dict(),
            [mock.call(_LOG_ERROR_FAIL_SEPARATOR, "key:val,key2:val2 key3:1234.23")],
        ),
        (
            "key:val key2:val2 key3: ",
            dict(key="val", key2="val2"),
            [
                mock.call(_LOG_ERROR_MALFORMED_TAG, "key3:", "key:val key2:val2 key3: "),
                mock.call(_LOG_ERROR_MALFORMED_TAG, "", "key:val key2:val2 key3: "),
            ],
        ),
        (
            "key:val key2:val 2",
            dict(key="val", key2="val"),
            [mock.call(_LOG_ERROR_MALFORMED_TAG, "2", "key:val key2:val 2")],
        ),
        (
            "key: val key2:val2 key3:val3",
            {"key2": "val2", "key3": "val3"},
            [
                mock.call(_LOG_ERROR_MALFORMED_TAG, "key:", "key: val key2:val2 key3:val3"),
                mock.call(_LOG_ERROR_MALFORMED_TAG, "val", "key: val key2:val2 key3:val3"),
            ],
        ),
        (
            "key:,key3:val1,",
            dict(key3="val1"),
            [
                mock.call(_LOG_ERROR_MALFORMED_TAG, "key:", "key:,key3:val1,"),
                mock.call(_LOG_ERROR_MALFORMED_TAG, "", "key:,key3:val1,"),
            ],
        ),
        (
            ",",
            dict(),
            [
                mock.call(_LOG_ERROR_MALFORMED_TAG, "", ","),
                mock.call(_LOG_ERROR_MALFORMED_TAG, "", ","),
            ],
        ),
        (
            ":,:",
            dict(),
            [
                mock.call(_LOG_ERROR_MALFORMED_TAG, ":", ":,:"),
                mock.call(_LOG_ERROR_MALFORMED_TAG, ":", ":,:"),
            ],
        ),
        (
            "key,key2:val1",
            dict(key2="val1"),
            [mock.call(_LOG_ERROR_MALFORMED_TAG, "key", "key,key2:val1")],
        ),
        ("key2:val1:", dict(), [mock.call(_LOG_ERROR_MALFORMED_TAG_STRING, "key2:val1:")]),
        (
            "key,key2,key3",
            dict(),
            [
                mock.call(_LOG_ERROR_MALFORMED_TAG, "key", "key,key2,key3"),
                mock.call(_LOG_ERROR_MALFORMED_TAG, "key2", "key,key2,key3"),
                mock.call(_LOG_ERROR_MALFORMED_TAG, "key3", "key,key2,key3"),
            ],
        ),
    ],
)
def test_parse_env_tags(tag_str, expected_tags, error_calls):
    with mock.patch("ddtrace.internal.utils.formats.log") as log:
        tags = parse_tags_str(tag_str)
        assert tags == expected_tags
        if error_calls:
            assert log.error.call_count == len(error_calls)
            log.error.assert_has_calls(error_calls)
        else:
            assert log.error.call_count == 0


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
        if sys.version_info >= (3, 10, 0):
            assert "functools.partial" == func_name(minus_two)
        else:
            assert "partial" == func_name(minus_two)
        assert 10 == plus_three(7)
        assert "tests.tracer.test_utils.<lambda>" == func_name(plus_three)


@pytest.mark.parametrize(
    "args,kwargs,pos,kw,expected",
    [
        ([], {"foo": 42, "bar": "snafu"}, 0, "foo", 42),
        ([], {"foo": 42, "bar": "snafu"}, 1, "bar", "snafu"),
        ([42], {"bar": "snafu"}, 0, "foo", 42),
        ([42, "snafu"], {}, 1, "bar", "snafu"),
    ],
)
def test_infer_arg_value_hit(args, kwargs, pos, kw, expected):
    assert get_argument_value(args, kwargs, pos, kw) == expected


@pytest.mark.parametrize(
    "args,kwargs,pos,kw",
    [
        ([], {}, 0, "foo"),
        ([], {}, 1, "bar"),
    ],
)
def test_infer_arg_value_miss(args, kwargs, pos, kw):
    with pytest.raises(ArgumentError) as e:
        get_argument_value(args, kwargs, pos, kw)
        assert e.value == "%s (at position %d)" % (kw, pos)


@pytest.mark.parametrize(
    "args,kwargs,pos,kw,new_value",
    [
        ((), {"foo": 42, "bar": "snafu"}, 0, "foo", 4442),
        ((), {"foo": 42, "bar": "snafu"}, 1, "bar", "new_snafu"),
        ((42,), {"bar": "snafu"}, 0, "foo", 442),
        ((42, "snafu"), {}, 1, "bar", "snafu_new"),
    ],
)
def test_set_argument_value(args, kwargs, pos, kw, new_value):
    new_args, new_kwargs = set_argument_value(args, kwargs, pos, kw, new_value)

    if kw in kwargs:
        assert new_kwargs[kw] == new_value
    else:
        assert new_args[pos] == new_value


@pytest.mark.parametrize(
    "args,kwargs,pos,kw,value",
    [
        ([], {}, 0, "foo", "val"),
        ([], {}, 1, "bar", "val"),
    ],
)
def test_set_invalid_argument_value(args, kwargs, pos, kw, value):
    with pytest.raises(ArgumentError) as e:
        set_argument_value(args, kwargs, pos, kw, value)
        assert e.value == "%s (at position %d) is invalid" % (kw, pos)


def cached_test_recipe(expensive, cheap, witness, cache_size):
    assert cheap("Foo") == expensive("Foo")
    assert cheap("Foo") == expensive("Foo")

    witness.assert_called_with("Foo")
    assert witness.call_count == 1

    cheap.invalidate()

    for i in range(cache_size >> 1):
        cheap("Foo%d" % i)

    assert witness.call_count == 1 + (cache_size >> 1)

    for i in range(cache_size):
        cheap("Foo%d" % i)

    assert witness.call_count == 1 + cache_size

    MAX_FOO = "Foo%d" % (cache_size - 1)

    cheap("last drop")  # Forces least frequent elements out of the cache
    assert witness.call_count == 2 + cache_size

    cheap(MAX_FOO)  # Check MAX_FOO was dropped
    assert witness.call_count == 3 + cache_size

    cheap("last drop")  # Check last drop was retained
    assert witness.call_count == 3 + cache_size


def test_cached():
    witness = mock.Mock()
    cache_size = 128

    def expensive(key):
        return key[::-1].lower()

    @cached(cache_size)
    def cheap(key):
        witness(key)
        return expensive(key)

    cached_test_recipe(expensive, cheap, witness, cache_size)


def test_cachedmethod():
    witness = mock.Mock()
    cache_size = 128

    def expensive(key):
        return key[::-1].lower()

    class Foo(object):
        @cachedmethod(cache_size)
        def cheap(self, key):
            witness(key)
            return expensive(key)

    cached_test_recipe(expensive, Foo().cheap, witness, cache_size)


@pytest.mark.parametrize(
    "version_str,expected",
    [
        ("5", (5, 0, 0)),
        ("0.5", (0, 5, 0)),
        ("0.5.0", (0, 5, 0)),
        ("1.0.0", (1, 0, 0)),
        ("1.2.0", (1, 2, 0)),
        ("1.2.8", (1, 2, 8)),
        ("2.0.0rc1", (2, 0, 0)),
        ("2.0.0-rc1", (2, 0, 0)),
        ("2.0.0 here be dragons", (2, 0, 0)),
        ("2020.6.19", (2020, 6, 19)),
        ("beta 1.0.0", (0, 0, 0)),
        ("no version found", (0, 0, 0)),
        ("", (0, 0, 0)),
    ],
)
def test_parse_version(version_str, expected):
    # type: (str, typing.Tuple[int, int, int]) -> None
    """Ensure parse_version helper properly parses versions"""
    assert parse_version(version_str) == expected
