import json

import pytest

from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking._context import create_context
from ddtrace.appsec._iast._taint_tracking._taint_objects import is_pyobject_tainted
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
from ddtrace.appsec._iast._taint_utils import LazyTaintDict
from ddtrace.appsec._iast._taint_utils import LazyTaintList
from tests.utils import override_global_config


def setup():
    create_context()


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


@pytest.mark.parametrize("input_jsonstr, res_type, tainted_type", TEST_INPUTS)
def test_taint_json(iast_context_defaults, input_jsonstr, res_type, tainted_type):
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

        assert is_fully_tainted(res)


@pytest.mark.parametrize("input_jsonstr, res_type, tainted_type", TEST_INPUTS)
def test_taint_json_no_taint(iast_context_defaults, input_jsonstr, res_type, tainted_type):
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
