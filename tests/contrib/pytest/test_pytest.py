import json
import os
import sys

from hypothesis import given
from hypothesis import strategies as st
import pytest

from ddtrace import Pin
from ddtrace.contrib.pytest.plugin import _json_encode
from ddtrace.ext import test
from tests.utils import TracerTestCase


class TestPytest(TracerTestCase):
    @pytest.fixture(autouse=True)
    def fixtures(self, testdir, monkeypatch):
        self.testdir = testdir
        self.monkeypatch = monkeypatch

    def inline_run(self, *args):
        """Execute test script with test tracer."""

        class PinTracer:
            @staticmethod
            def pytest_configure(config):
                if Pin.get_from(config) is not None:
                    Pin.override(config, tracer=self.tracer)

        return self.testdir.inline_run(*args, plugins=[PinTracer()])

    def subprocess_run(self, *args):
        """Execute test script with test tracer."""
        return self.testdir.runpytest_subprocess(*args)

    @pytest.mark.skipif(sys.version_info[0] == 2, reason="Triggers a bug with coverage, sqlite and Python 2")
    def test_patch_all(self):
        """Test with --ddtrace-patch-all."""
        py_file = self.testdir.makepyfile(
            """
            import ddtrace.monkey

            def test_patched_all():
                assert ddtrace.monkey._PATCHED_MODULES
        """
        )
        file_name = os.path.basename(py_file.strpath)
        rec = self.inline_run("--ddtrace-patch-all", file_name)
        rec.assertoutcome(passed=1)
        spans = self.pop_spans()

        assert len(spans) == 0

    @pytest.mark.skipif(sys.version_info[0] == 2, reason="Triggers a bug with coverage, sqlite and Python 2")
    def test_patch_all_init(self):
        """Test with ddtrace-patch-all via ini."""
        self.testdir.makefile(".ini", pytest="[pytest]\nddtrace-patch-all=1\n")
        py_file = self.testdir.makepyfile(
            """
            import ddtrace.monkey

            def test_patched_all():
                assert ddtrace.monkey._PATCHED_MODULES
        """
        )
        file_name = os.path.basename(py_file.strpath)
        rec = self.inline_run(file_name)
        rec.assertoutcome(passed=1)
        spans = self.pop_spans()

        assert len(spans) == 0

    def test_disabled(self):
        """Test without --ddtrace."""
        py_file = self.testdir.makepyfile(
            """
            import pytest

            def test_no_trace(ddspan):
                assert ddspan is None
        """
        )
        file_name = os.path.basename(py_file.strpath)
        rec = self.inline_run(file_name)
        rec.assertoutcome(passed=1)
        spans = self.pop_spans()

        assert len(spans) == 0

    def test_ini(self):
        """Test ini config."""
        self.testdir.makefile(".ini", pytest="[pytest]\nddtrace=1\n")
        py_file = self.testdir.makepyfile(
            """
            import pytest

            def test_ini(ddspan):
                assert ddspan is not None
        """
        )
        file_name = os.path.basename(py_file.strpath)
        rec = self.inline_run(file_name)
        rec.assertoutcome(passed=1)
        spans = self.pop_spans()

        assert len(spans) == 1

    def test_parameterize_case(self):
        """Test parametrize case."""
        py_file = self.testdir.makepyfile(
            """
            import pytest

            class A:
                def __init__(self, name, value):
                    self.name = name
                    self.value = value

            def item_param():
                return 42

            @pytest.mark.parametrize(
                'item',
                [
                    1,
                    2,
                    3,
                    4,
                    pytest.param(A("test_name", "value"), marks=pytest.mark.skip),
                    pytest.param(A("test_name", A("inner_name", "value")), marks=pytest.mark.skip),
                    pytest.param({"a": A("test_name", "value"), "b": [1, 2, 3]}, marks=pytest.mark.skip),
                    pytest.param([1, 2, 3], marks=pytest.mark.skip),
                    pytest.param(item_param, marks=pytest.mark.skip)
                ]
            )
            class Test1(object):
                def test_1(self, item):
                    assert item in {1, 2, 3}
        """
        )
        file_name = os.path.basename(py_file.strpath)
        rec = self.inline_run("--ddtrace", file_name)
        rec.assertoutcome(passed=3, failed=1, skipped=5)
        spans = self.pop_spans()

        expected_params = [
            1,
            2,
            3,
            4,
            {"name": "test_name", "value": "value"},
            {"name": "test_name", "value": {"name": "inner_name", "value": "value"}},
            {"a": {"name": "test_name", "value": "value"}, "b": [1, 2, 3]},
            [1, 2, 3],
        ]
        assert len(spans) == 9
        for i in range(len(spans) - 1):
            extracted_params = json.loads(spans[i].meta[test.PARAMETERS])
            assert extracted_params == {"arguments": {"item": expected_params[i]}, "metadata": {}}
        assert "<function item_param at 0x" in json.loads(spans[8].meta[test.PARAMETERS])["arguments"]["item"]

    def test_skip(self):
        """Test parametrize case."""
        py_file = self.testdir.makepyfile(
            """
            import pytest

            @pytest.mark.skip(reason="decorator")
            def test_decorator():
                pass

            def test_body():
                pytest.skip("body")
        """
        )
        file_name = os.path.basename(py_file.strpath)
        rec = self.inline_run("--ddtrace", file_name)
        rec.assertoutcome(skipped=2)
        spans = self.pop_spans()

        assert len(spans) == 2
        assert spans[0].get_tag(test.STATUS) == test.Status.SKIP.value
        assert spans[0].get_tag(test.SKIP_REASON) == "decorator"
        assert spans[1].get_tag(test.STATUS) == test.Status.SKIP.value
        assert spans[1].get_tag(test.SKIP_REASON) == "body"

    def test_xfail_fails(self):
        """Test xfail (expected failure) which fails, should be marked as pass."""
        py_file = self.testdir.makepyfile(
            """
            import pytest

            @pytest.mark.xfail(reason="test should fail")
            def test_should_fail():
                assert 0
        """
        )
        file_name = os.path.basename(py_file.strpath)
        rec = self.inline_run("--ddtrace", file_name)
        # pytest records xfail as skipped
        rec.assertoutcome(skipped=1)
        spans = self.pop_spans()

        assert len(spans) == 1
        assert spans[0].get_tag(test.STATUS) == test.Status.PASS.value
        assert spans[0].get_tag(test.RESULT) == test.Status.XFAIL.value
        assert spans[0].get_tag(test.XFAIL_REASON) == "test should fail"

    def test_xpass_not_strict(self):
        """Test xpass (unexpected passing) with strict=False, should be marked as pass."""
        py_file = self.testdir.makepyfile(
            """
            import pytest

            @pytest.mark.xfail(reason="test should fail")
            def test_should_fail():
                pass
        """
        )
        file_name = os.path.basename(py_file.strpath)
        rec = self.inline_run("--ddtrace", file_name)
        rec.assertoutcome(passed=1)
        spans = self.pop_spans()

        assert len(spans) == 1
        assert spans[0].get_tag(test.STATUS) == test.Status.PASS.value
        assert spans[0].get_tag(test.RESULT) == test.Status.XPASS.value
        assert spans[0].get_tag(test.XFAIL_REASON) == "test should fail"

    def test_xpass_strict(self):
        """Test xpass (unexpected passing) with strict=True, should be marked as fail."""
        py_file = self.testdir.makepyfile(
            """
            import pytest

            @pytest.mark.xfail(reason="test should fail", strict=True)
            def test_should_fail():
                pass
        """
        )
        file_name = os.path.basename(py_file.strpath)
        rec = self.inline_run("--ddtrace", file_name)
        rec.assertoutcome(failed=1)
        spans = self.pop_spans()

        assert len(spans) == 1
        assert spans[0].get_tag(test.STATUS) == test.Status.FAIL.value
        assert spans[0].get_tag(test.RESULT) == test.Status.XPASS.value
        # Note: xpass (strict=True) does not mark the reason with result.wasxfail but into result.longrepr,
        # however this provides the entire traceback/error into longrepr.
        assert "test should fail" in spans[0].get_tag(test.XFAIL_REASON)

    def test_tags(self):
        """Test ddspan tags."""
        py_file = self.testdir.makepyfile(
            """
            import pytest

            @pytest.mark.dd_tags(mark="dd_tags")
            def test_fixture(ddspan):
                assert ddspan is not None
                ddspan.set_tag("world", "hello")
        """
        )
        file_name = os.path.basename(py_file.strpath)
        rec = self.inline_run("--ddtrace", file_name)
        rec.assertoutcome(passed=1)
        spans = self.pop_spans()

        assert len(spans) == 1
        assert spans[0].get_tag("world") == "hello"
        assert spans[0].get_tag("mark") == "dd_tags"
        assert spans[0].get_tag(test.STATUS) == test.Status.PASS.value

    def test_default_service_name(self):
        """Test default service name."""
        py_file = self.testdir.makepyfile(
            """
            def test_service(ddspan):
                assert True
        """
        )
        file_name = os.path.basename(py_file.strpath)
        rec = self.inline_run("--ddtrace", file_name)
        rec.assertoutcome(passed=1)
        spans = self.pop_spans()

        assert len(spans) == 1
        assert spans[0].service == "pytest"
        assert spans[0].name == "pytest.test"

    def test_dd_service_name(self):
        """Test integration service name."""
        self.monkeypatch.setenv("DD_SERVICE", "mysvc")
        if "DD_PYTEST_SERVICE" in os.environ:
            self.monkeypatch.delenv("DD_PYTEST_SERVICE")

        py_file = self.testdir.makepyfile(
            """
            import os

            def test_service(ddspan):
                assert 'mysvc' == os.getenv('DD_SERVICE')
                assert os.getenv('DD_PYTEST_SERVICE') is None
                assert 'mysvc' == ddspan.service
        """
        )
        file_name = os.path.basename(py_file.strpath)
        rec = self.subprocess_run("--ddtrace", file_name)
        assert 0 == rec.ret

    def test_dd_pytest_service_name(self):
        """Test integration service name."""
        self.monkeypatch.setenv("DD_SERVICE", "mysvc")
        self.monkeypatch.setenv("DD_PYTEST_SERVICE", "pymysvc")
        self.monkeypatch.setenv("DD_PYTEST_OPERATION_NAME", "mytest")

        py_file = self.testdir.makepyfile(
            """
            import os

            def test_service(ddspan):
                assert 'mysvc' == os.getenv('DD_SERVICE')
                assert 'pymysvc' == os.getenv('DD_PYTEST_SERVICE')
                assert 'pymysvc' == ddspan.service
                assert 'mytest' == ddspan.name
        """
        )
        file_name = os.path.basename(py_file.strpath)
        rec = self.subprocess_run("--ddtrace", file_name)
        assert 0 == rec.ret


class A(object):
    def __init__(self, name, value):
        self.name = name
        self.value = value


simple_types = [st.none(), st.booleans(), st.text(), st.integers(), st.floats(allow_infinity=False, allow_nan=False)]
complex_types = [st.functions(), st.dates(), st.decimals(), st.builds(A, name=st.text(), value=st.integers())]


@given(
    st.dictionaries(
        st.text(),
        st.one_of(
            st.lists(st.one_of(*simple_types)), st.dictionaries(st.text(), st.one_of(*simple_types)), *simple_types
        ),
    )
)
def test_custom_json_encoding_simple_types(obj):
    """Ensures the _json.encode helper encodes simple objects."""
    encoded = _json_encode(obj)
    decoded = json.loads(encoded)
    assert obj == decoded


@given(
    st.dictionaries(
        st.text(),
        st.one_of(
            st.lists(st.one_of(*complex_types)), st.dictionaries(st.text(), st.one_of(*complex_types)), *complex_types
        ),
    )
)
def test_custom_json_encoding_python_objects(obj):
    """Ensures the _json_encode helper encodes complex objects into dicts of inner values or a string representation."""
    encoded = _json_encode(obj)
    obj = json.loads(
        json.dumps(obj, default=lambda x: getattr(x, "__dict__", None) if getattr(x, "__dict__", None) else repr(x))
    )
    decoded = json.loads(encoded)
    assert obj == decoded


def test_custom_json_encoding_side_effects():
    """Ensures the _json_encode helper encodes objects with side effects (getattr, repr) without raising exceptions."""
    dict_side_effect = Exception("side effect __dict__")
    repr_side_effect = Exception("side effect __repr__")

    class B(object):
        def __getattribute__(self, item):
            if item == "__dict__":
                raise dict_side_effect
            raise AttributeError()

    class C(object):
        def __repr__(self):
            raise repr_side_effect

    obj = {"b": B(), "c": C()}
    encoded = _json_encode(obj)
    decoded = json.loads(encoded)
    assert decoded["b"] == repr(dict_side_effect)
    assert decoded["c"] == repr(repr_side_effect)
