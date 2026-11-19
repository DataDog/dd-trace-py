import builtins
import json
import sys

import pytest

from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
from ddtrace.appsec._iast._taint_tracking._taint_objects_base import is_pyobject_tainted
from ddtrace.appsec._iast._taint_tracking.aspects import json_loads_aspect
from ddtrace.appsec._iast._taint_utils import LazyTaintDict
from ddtrace.appsec._iast._taint_utils import LazyTaintList
from tests.utils import override_global_config


def is_fully_tainted(obj):
    if not obj:
        return True
    if isinstance(obj, str):
        return is_pyobject_tainted(obj)
    if isinstance(obj, list):
        return all(is_fully_tainted(e) for e in obj)
    if isinstance(obj, (dict, getattr(builtins, "frozendict", dict))):
        return all(is_fully_tainted(k) and is_fully_tainted(e) for k, e in obj.items())
    return True


TEST_INPUTS = [
    ('"tainted string"', str, str),
    ('{"tainted_key":"tainted_string"}', dict, LazyTaintDict),
    ('{"nested":{"key":"value"}}', dict, LazyTaintDict),
    ('[{"key":[1,2,3,"value"]}]', list, LazyTaintList),
]


@pytest.mark.parametrize("input_jsonstr, res_type, tainted_type", TEST_INPUTS)
@pytest.mark.parametrize("hook", [None, "object_hook", "object_pairs_hook"])
def test_taint_json(iast_context_defaults, input_jsonstr, res_type, tainted_type, mapping_factory, hook):
    """Test that json_loads_aspect propagates taint from tainted input strings."""
    with override_global_config(dict(_iast_enabled=True)):
        input_str = taint_pyobject(
            pyobject=input_jsonstr,
            source_name="request_body",
            source_value="hello",
            source_origin=OriginType.PARAMETER,
        )
        assert is_pyobject_tainted(input_str)

        res = json_loads_aspect(input_str, **({hook: mapping_factory} if hook else {}))

        assert type(res) is (mapping_factory if hook and res_type is dict else res_type)
        assert is_fully_tainted(res)


@pytest.mark.parametrize("input_jsonstr, res_type, tainted_type", TEST_INPUTS)
def test_taint_json_no_taint(iast_context_defaults, input_jsonstr, res_type, tainted_type):
    """Test that json_loads_aspect does not taint results from untainted input."""
    with override_global_config(dict(_iast_enabled=True)):
        input_str = input_jsonstr
        assert not is_pyobject_tainted(input_str)

        res = json_loads_aspect(input_str)

        # this must be expected type
        assert isinstance(res, res_type)
        assert type(res) is res_type

        # this must not pass as tainted type
        if res_type is not tainted_type:
            assert not isinstance(res, tainted_type)

        assert not is_fully_tainted(res)


@pytest.mark.parametrize("input_jsonstr, res_type, tainted_type", TEST_INPUTS)
def test_plain_json_loads_not_tainted(iast_context_defaults, input_jsonstr, res_type, tainted_type):
    """Test that plain json.loads (not going through the aspect) does NOT taint results,
    confirming that json.loads is no longer globally wrapped.
    """
    with override_global_config(dict(_iast_enabled=True)):
        input_str = taint_pyobject(
            pyobject=input_jsonstr,
            source_name="request_body",
            source_value="hello",
            source_origin=OriginType.PARAMETER,
        )
        assert is_pyobject_tainted(input_str)

        res = json.loads(input_str)

        # Plain json.loads should NOT produce tainted results since it's no longer globally wrapped
        if isinstance(res, (dict, list)):
            assert not is_fully_tainted(res) or not res
        elif isinstance(res, str):
            assert not is_pyobject_tainted(res)


@pytest.mark.skipif(sys.version_info < (3, 15), reason="Requires Python 3.15 frozendict")
def test_json_loads_does_not_reconstruct_custom_frozendict(iast_context_defaults):
    class Frozen(builtins.frozendict):
        calls = 0

        def __new__(cls, value, schema):
            cls.calls += 1
            result = super().__new__(cls, value)
            result.schema = schema
            return result

    schema = object()
    body = taint_pyobject('{"key":"input"}', "body", "json", OriginType.BODY)
    result = json_loads_aspect(body, object_hook=lambda value: Frozen(value, schema))
    assert type(result) is Frozen
    assert result.schema is schema
    assert Frozen.calls == 1


@pytest.mark.skipif(sys.version_info < (3, 15), reason="Requires Python 3.15 frozendict")
@pytest.mark.subprocess(env={"DD_IAST_ENABLED": "true", "_DD_IAST_LAZY_TAINT": "true"}, err=None)
def test_json_loads_frozendict_preserves_legacy_lazy_behavior():
    import builtins

    from ddtrace.appsec._iast._taint_tracking import OriginType
    from ddtrace.appsec._iast._taint_tracking import initialize_native_state
    from ddtrace.appsec._iast._taint_tracking._context import finish_request_context
    from ddtrace.appsec._iast._taint_tracking._context import start_request_context
    from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
    from ddtrace.appsec._iast._taint_tracking.aspects import json_loads_aspect
    from ddtrace.appsec._iast._taint_utils import LazyTaintDict
    from ddtrace.appsec._iast_context import IAST_CONTEXT
    from ddtrace.internal.settings.asm import config

    assert config._iast_lazy_taint
    initialize_native_state()
    context = start_request_context()
    assert context is not None
    IAST_CONTEXT.set(context)
    try:
        parsed = []

        def freeze(value):
            result = builtins.frozendict(value)
            parsed.append(result)
            return result

        body = taint_pyobject('{"key":"input"}', "body", "json", OriginType.BODY)
        result = json_loads_aspect(body, object_hook=freeze)
        assert result is parsed[0]
        assert type(result) is builtins.frozendict
        assert hash(result) == hash(builtins.frozendict(key="input"))
        assert isinstance(json_loads_aspect(body), LazyTaintDict)
    finally:
        finish_request_context(context)
        IAST_CONTEXT.set(None)
