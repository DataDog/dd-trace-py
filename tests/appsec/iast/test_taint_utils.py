from unittest import mock

import pytest

from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking._taint_objects import is_pyobject_tainted
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
from ddtrace.appsec._iast._taint_utils import LazyTaintDict
from ddtrace.appsec._iast._taint_utils import LazyTaintList
from ddtrace.appsec._iast._taint_utils import check_tainted_dbapi_args


@pytest.fixture
def lazy_taint_json_patch():
    from ddtrace.appsec._iast._patches.json_tainting import patched_json_encoder_default
    from ddtrace.appsec._iast._patches.json_tainting import try_unwrap
    from ddtrace.appsec._iast._patches.json_tainting import try_wrap_function_wrapper

    try_wrap_function_wrapper("json.encoder", "JSONEncoder.default", patched_json_encoder_default)
    try_wrap_function_wrapper("simplejson.encoder", "JSONEncoder.default", patched_json_encoder_default)
    yield
    try_unwrap("json.encoder", "JSONEncoder.default")
    try_unwrap("simplejson.encoder", "JSONEncoder.default")


def test_tainted_types(iast_context_defaults):
    tainted = taint_pyobject(
        pyobject="hello", source_name="request_body", source_value="hello", source_origin=OriginType.PARAMETER
    )
    assert is_pyobject_tainted(tainted)

    tainted = taint_pyobject(
        pyobject=b"hello", source_name="request_body", source_value="hello", source_origin=OriginType.PARAMETER
    )
    assert is_pyobject_tainted(tainted)

    tainted = taint_pyobject(
        bytearray("hello", encoding="utf-8"),
        source_name="request_body",
        source_value="hello",
        source_origin=OriginType.PARAMETER,
    )
    assert is_pyobject_tainted(tainted)

    # Not tainted as string is empty
    not_tainted = taint_pyobject(
        "", source_name="request_body", source_value="hello", source_origin=OriginType.PARAMETER
    )
    assert not is_pyobject_tainted(not_tainted)

    # Not tainted as not text type
    not_tainted = taint_pyobject(
        123456, source_name="request_body", source_value="hello", source_origin=OriginType.PARAMETER
    )
    assert not is_pyobject_tainted(not_tainted)

    # Not tainted as not text type
    not_tainted = taint_pyobject(
        1234.56, source_name="request_body", source_value="hello", source_origin=OriginType.PARAMETER
    )
    assert not is_pyobject_tainted(not_tainted)

    # Not tainted as not text type
    not_tainted = taint_pyobject(
        {"a": "1", "b": 2}, source_name="request_body", source_value="hello", source_origin=OriginType.PARAMETER
    )
    assert not is_pyobject_tainted(not_tainted)

    # Not tainted as not text type
    not_tainted = taint_pyobject(
        ["a", "1", "b", 2], source_name="request_body", source_value="hello", source_origin=OriginType.PARAMETER
    )
    assert not is_pyobject_tainted(not_tainted)


def test_tainted_getitem(iast_context_defaults):
    knights = {"gallahad": "".join(("the pure", "")), "robin": "".join(("the brave", "")), "not string": 1}
    tainted_knights = LazyTaintDict(
        {"gallahad": "".join(("the pure", "")), "robin": "".join(("the brave", "")), "not string": 1},
        origins=(OriginType.PARAMETER, OriginType.PARAMETER),
    )

    # Strings are tainted, but integers are not
    assert is_pyobject_tainted(tainted_knights["gallahad"])
    assert not is_pyobject_tainted(tainted_knights["not string"])

    # Regular dict is not affected
    assert not is_pyobject_tainted(knights["gallahad"])

    # KeyError is raised if the key is not found
    with pytest.raises(KeyError):
        knights["arthur"]
    with pytest.raises(KeyError):
        tainted_knights["arthur"]


def test_tainted_get(iast_context_defaults):
    knights = {"gallahad": "".join(("the pure", "")), "robin": "".join(("the brave", "")), "not string": 1}
    tainted_knights = LazyTaintDict(
        {"gallahad": "".join(("the pure", "")), "robin": "".join(("the brave", "")), "not string": 1},
        origins=(OriginType.PARAMETER, OriginType.PARAMETER),
    )

    # Not-existing key returns None or default
    arthur = knights.get("arthur")
    assert arthur is None
    arthur = tainted_knights.get("arthur")
    assert arthur is None
    arthur = tainted_knights.get("arthur", "default")
    assert arthur == "default"
    assert not is_pyobject_tainted(arthur)

    # Integers are not tainted
    not_string = tainted_knights.get("not string")
    assert not is_pyobject_tainted(not_string)

    # String-like values are tainted
    tainted_robin = tainted_knights.get("robin")
    assert tainted_robin is not None
    assert is_pyobject_tainted(tainted_robin)

    # Regular dict is not affected
    robin = knights.get("robin")
    assert not is_pyobject_tainted(robin)


def test_tainted_items(iast_context_defaults):
    knights = {"gallahad": "".join(("the pure", "")), "robin": "".join(("the brave", ""))}
    tainted_knights = LazyTaintDict(
        {"gallahad": "".join(("the pure", "")), "robin": "".join(("the brave", ""))},
        origins=(OriginType.PARAMETER, OriginType.PARAMETER),
    )

    # Keys and values are tainted if string-like
    for k, v in tainted_knights.items():
        assert is_pyobject_tainted(k)
        assert is_pyobject_tainted(v)

    # Regular dict is not affected
    for k, v in knights.items():
        assert not is_pyobject_tainted(k)
        assert not is_pyobject_tainted(v)


def test_tainted_keys_and_values(iast_context_defaults):
    knights = {"gallahad": "".join(("the pure", "")), "robin": "".join(("the brave", ""))}
    tainted_knights = LazyTaintDict(
        {"gallahad": "".join(("the pure", "")), "robin": "".join(("the brave", ""))},
        origins=(OriginType.PARAMETER, OriginType.PARAMETER),
    )

    # Keys are tainted if string-like
    for k in tainted_knights.keys():
        assert is_pyobject_tainted(k)

    # Values are tainted if string-like
    for v in tainted_knights.values():
        assert is_pyobject_tainted(v)

    # Regular dict is not affected
    for v in knights.values():
        assert not is_pyobject_tainted(v)


def test_recursivity(iast_context_defaults):
    tainted_dict = LazyTaintDict(
        {
            "tr_key_001": ["tr_val_001", "tr_val_002", "tr_val_003", {"tr_key_005": "tr_val_004"}],
            "tr_key_002": {"tr_key_003": {"tr_key_004": "tr_val_005"}},
        },
        origins=(OriginType.PARAMETER, OriginType.PARAMETER),
    )

    def check_taint(v):
        if isinstance(v, str):
            assert is_pyobject_tainted(v)
        elif isinstance(v, dict):
            for k, ev in v.items():
                assert is_pyobject_tainted(k)
                check_taint(ev)
        elif isinstance(v, list):
            for ev in v:
                check_taint(ev)

    check_taint(tainted_dict)


def test_checked_tainted_args(iast_context_defaults):
    cursor = mock.Mock()
    cursor.execute.__name__ = "execute"
    cursor.executemany.__name__ = "executemany"

    arg = "nobody expects the spanish inquisition"

    tainted_arg = taint_pyobject(arg, source_name="request_body", source_value=arg, source_origin=OriginType.PARAMETER)

    untainted_arg = "gallahad the pure"

    # Returns False: Untainted first argument
    assert not check_tainted_dbapi_args(
        args=(untainted_arg,), kwargs=None, integration_name="sqlite", method=cursor.execute
    )

    # Returns False: Untainted first argument
    assert not check_tainted_dbapi_args(
        args=(untainted_arg, tainted_arg), kwargs=None, integration_name="sqlite", method=cursor.execute
    )

    # Returns False: Integration name not in list
    assert not check_tainted_dbapi_args(
        args=(tainted_arg,),
        kwargs=None,
        integration_name="nosqlite",
        method=cursor.execute,
    )

    # Returns False: Wrong function name
    assert not check_tainted_dbapi_args(
        args=(tainted_arg,),
        kwargs=None,
        integration_name="sqlite",
        method=cursor.executemany,
    )

    # Returns True:
    assert check_tainted_dbapi_args(
        args=(tainted_arg, untainted_arg), kwargs=None, integration_name="sqlite", method=cursor.execute
    )

    # Returns True:
    assert check_tainted_dbapi_args(
        args=(tainted_arg, untainted_arg), kwargs=None, integration_name="mysql", method=cursor.execute
    )

    # Returns True:
    assert check_tainted_dbapi_args(
        args=(tainted_arg, untainted_arg), kwargs=None, integration_name="psycopg", method=cursor.execute
    )


@pytest.mark.usefixtures("lazy_taint_json_patch")
def test_json_encode_dict(iast_context_defaults):
    import json

    tainted_dict = LazyTaintDict(
        {
            "tr_key_001": ["tr_val_001", "tr_val_002", "tr_val_003", {"tr_key_005": "tr_val_004"}],
            "tr_key_002": {"tr_key_003": {"tr_key_004": "tr_val_005"}},
        },
        origins=(OriginType.PARAMETER, OriginType.PARAMETER),
    )

    assert json.dumps(tainted_dict) == (
        '{"tr_key_001": ["tr_val_001", "tr_val_002", "tr_val_003", '
        '{"tr_key_005": "tr_val_004"}], "tr_key_002": {"tr_key_003": {"tr_key_004": "tr_val_005"}}}'
    )


@pytest.mark.usefixtures("lazy_taint_json_patch")
def test_json_encode_list(iast_context_defaults):
    import json

    tainted_list = LazyTaintList(
        ["tr_val_001", "tr_val_002", "tr_val_003", {"tr_key_005": "tr_val_004"}],
        origins=(OriginType.PARAMETER, OriginType.PARAMETER),
    )

    assert json.dumps(tainted_list) == '["tr_val_001", "tr_val_002", "tr_val_003", {"tr_key_005": "tr_val_004"}]'


@pytest.mark.usefixtures("lazy_taint_json_patch")
def test_simplejson_encode_dict(iast_context_defaults):
    import simplejson as json

    tainted_dict = LazyTaintDict(
        {
            "tr_key_001": ["tr_val_001", "tr_val_002", "tr_val_003", {"tr_key_005": "tr_val_004"}],
            "tr_key_002": {"tr_key_003": {"tr_key_004": "tr_val_005"}},
        },
        origins=(OriginType.PARAMETER, OriginType.PARAMETER),
    )

    assert json.dumps(tainted_dict) == (
        '{"tr_key_001": ["tr_val_001", "tr_val_002", "tr_val_003", '
        '{"tr_key_005": "tr_val_004"}], "tr_key_002": {"tr_key_003": {"tr_key_004": "tr_val_005"}}}'
    )


@pytest.mark.usefixtures("lazy_taint_json_patch")
def test_simplejson_encode_list(iast_context_defaults):
    import simplejson as json

    tainted_list = LazyTaintList(
        ["tr_val_001", "tr_val_002", "tr_val_003", {"tr_key_005": "tr_val_004"}],
        origins=(OriginType.PARAMETER, OriginType.PARAMETER),
    )

    assert json.dumps(tainted_list) == '["tr_val_001", "tr_val_002", "tr_val_003", {"tr_key_005": "tr_val_004"}]'


def test_taint_structure(iast_context_defaults):
    from ddtrace.appsec._iast._taint_utils import taint_structure

    d = {1: "foo"}
    tainted = taint_structure(d, OriginType.PARAMETER, OriginType.PARAMETER)
    assert is_pyobject_tainted(tainted[1])
