import json

import pytest

from tests.utils import override_global_config


try:
    from ddtrace.appsec.iast import oce
    from ddtrace.appsec.iast._taint_tracking import OriginType
    from ddtrace.appsec.iast._taint_tracking import create_context
    from ddtrace.appsec.iast._taint_tracking import is_pyobject_tainted
    from ddtrace.appsec.iast._taint_tracking import setup as taint_tracking_setup
    from ddtrace.appsec.iast._taint_tracking import taint_pyobject
    from ddtrace.appsec.iast._taint_utils import LazyTaintDict
    from ddtrace.appsec.iast._taint_utils import LazyTaintList
    from ddtrace.appsec.iast._taint_utils import _is_tainted_struct
    from ddtrace.appsec.iast._util import _is_python_version_supported as python_supported_by_iast
except (ImportError, AttributeError):
    pytest.skip("IAST not supported for this Python version", allow_module_level=True)


def setup():
    create_context()
    taint_tracking_setup(bytes.join, bytearray.join)
    oce._enabled = True


FIXTURES_PATH = "tests/appsec/iast/fixtures/weak_algorithms.py"


def is_fully_tainted(obj):
    if not obj:
        return True
    if isinstance(obj, str):
        return is_pyobject_tainted(obj)
    if isinstance(obj, list):
        return all(is_fully_tainted(e) for e in obj)
    if isinstance(obj, dict):
        return all(is_fully_tainted(k) and is_fully_tainted(e) for k, e in obj.items())
    return True


TEST_INPUTS = [
    ('"tainted string"', str, str),
    ('{"tainted_key":"tainted_string"}', dict, LazyTaintDict),
    ('[{"key":[1,2,3,"value"]}]', list, LazyTaintList),
]


@pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
@pytest.mark.parametrize("input_jsonstr, res_type, tainted_type", TEST_INPUTS)
def test_taint_json(iast_span_defaults, input_jsonstr, res_type, tainted_type):
    assert json._datadog_json_tainting_patch
    with override_global_config(dict(_iast_enabled=True)):
        input_str = taint_pyobject(
            pyobject=input_jsonstr,
            source_name="request_body",
            source_value="hello",
            source_origin=OriginType.PARAMETER,
        )
        assert is_pyobject_tainted(input_str)

        res = json.loads(input_str)
        assert isinstance(res, (str, bytes, bytearray)) or _is_tainted_struct(res)

        # this must pass as expected type
        assert isinstance(res, res_type)
        # this must be tainted type
        assert isinstance(res, tainted_type)
        assert type(res) is tainted_type

        assert is_fully_tainted(res)


@pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
@pytest.mark.parametrize("input_jsonstr, res_type, tainted_type", TEST_INPUTS)
def test_taint_json_no_taint(iast_span_defaults, input_jsonstr, res_type, tainted_type):
    with override_global_config(dict(_iast_enabled=True)):
        input_str = input_jsonstr
        assert not is_pyobject_tainted(input_str)

        res = json.loads(input_str)

        # this must be expected type
        assert isinstance(res, res_type)
        assert type(res) is res_type

        # this must not pass as tainted type
        if res_type is not tainted_type:
            assert not isinstance(res, tainted_type)

        assert not is_fully_tainted(res)
