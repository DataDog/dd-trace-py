import pytest

from ddtrace.appsec._iast._patch_modules import WrapFunctonsForIAST
from ddtrace.appsec._iast._patches.json_tainting import patched_json_encoder_default
from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
from ddtrace.appsec._iast._taint_tracking._taint_objects_base import is_pyobject_tainted
from ddtrace.appsec._iast._taint_utils import LazyTaintDict
from ddtrace.appsec._iast._taint_utils import LazyTaintList


@pytest.fixture
def lazy_taint_json_patch():
    iast_funcs = WrapFunctonsForIAST()

    iast_funcs.wrap_function("json.encoder", "JSONEncoder.default", patched_json_encoder_default)
    iast_funcs.wrap_function("simplejson.encoder", "JSONEncoder.default", patched_json_encoder_default)
    iast_funcs.patch()
    yield
    iast_funcs.testing_unpatch()


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


@pytest.mark.parametrize("structure_kind", ["eager", "lazy_dict", "lazy_list"])
@pytest.mark.parametrize("override", [False, True])
@pytest.mark.parametrize("text_type", [str, bytes, bytearray])
@pytest.mark.parametrize("difference", [None, "origin", "name", "value", "start", "length", "multiple"])
def test_taint_structure_source_override(iast_context_defaults, structure_kind, override, text_type, difference):
    from ddtrace.appsec._iast._taint_tracking import Source
    from ddtrace.appsec._iast._taint_tracking import TaintRange
    from ddtrace.appsec._iast._taint_tracking import VulnerabilityType
    from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject_with_ranges
    from ddtrace.appsec._iast._taint_tracking._taint_objects_base import get_tainted_ranges
    from ddtrace.appsec._iast._taint_utils import taint_structure
    from ddtrace.appsec._iast.secure_marks.base import add_secure_mark

    text = "http://dummy.location.com"
    source_name = "previous" if difference == "name" else "location"
    source_value = "previous value" if difference == "value" else text
    source_origin = OriginType.BODY if difference == "origin" else OriginType.PARAMETER
    value = taint_pyobject(
        text if text_type is str else text_type(text, "utf-8"),
        source_name=source_name,
        source_value=source_value,
        source_origin=source_origin,
    )
    source = Source(source_name, source_value, source_origin)
    start = 1 if difference == "start" else 0
    length = len(value) - 1 if difference in ("start", "length") else len(value)
    ranges = [TaintRange(start, length, source)]
    if difference == "multiple":
        ranges = [TaintRange(0, 7, source), TaintRange(7, len(value) - 7, source)]
    taint_pyobject_with_ranges(value, ranges)
    add_secure_mark(value, [VulnerabilityType.UNVALIDATED_REDIRECT])

    if structure_kind == "eager":
        result = taint_structure(
            {"location": value}, OriginType.PARAMETER_NAME, OriginType.PARAMETER, override_pyobject_tainted=override
        )["location"]
    elif structure_kind == "lazy_dict":
        result = LazyTaintDict(
            {"location": value},
            origins=(OriginType.PARAMETER_NAME, OriginType.PARAMETER),
            override_pyobject_tainted=override,
        )["location"]
    else:
        result = LazyTaintList(
            [value],
            origins=(OriginType.PARAMETER_NAME, OriginType.PARAMETER),
            override_pyobject_tainted=override,
            source_name="location",
        )[0]

    result_ranges = get_tainted_ranges(result)
    if override and (difference is not None or text_type is bytearray):
        assert len(result_ranges) == 1
        taint_range = result_ranges[0]
        assert taint_range.start == 0
        assert taint_range.length == len(value)
        assert taint_range.source.name == "location"
        assert taint_range.source.value == text
        assert taint_range.source.origin == OriginType.PARAMETER
        assert not taint_range.has_secure_mark(VulnerabilityType.UNVALIDATED_REDIRECT)
    else:
        assert result is value
        assert len(result_ranges) == len(ranges)
        for actual, expected in zip(result_ranges, ranges):
            assert actual.start == expected.start
            assert actual.length == expected.length
            assert actual.source == expected.source
            assert actual.has_secure_mark(VulnerabilityType.UNVALIDATED_REDIRECT) == (text_type is not bytearray)


@pytest.mark.parametrize("structure_kind", ["eager", "lazy_dict", "lazy_list"])
@pytest.mark.parametrize("text_type", [str, bytes])
def test_taint_structure_undecodable_source_value(iast_context_defaults, monkeypatch, structure_kind, text_type):
    from ddtrace.appsec._iast._taint_tracking import VulnerabilityType
    from ddtrace.appsec._iast._taint_tracking._native import reset_source_truncation_cache
    from ddtrace.appsec._iast._taint_tracking._taint_objects_base import get_tainted_ranges
    from ddtrace.appsec._iast._taint_utils import taint_structure
    from ddtrace.appsec._iast.secure_marks.base import add_secure_mark

    # Exercise the native default, instead of the fixture's increased truncation limit.
    monkeypatch.delenv("DD_IAST_TRUNCATION_MAX_VALUE_LENGTH", raising=False)
    reset_source_truncation_cache()
    text = "€" * 84
    value = taint_pyobject(
        text if text_type is str else text.encode("utf-8"),
        source_name="location",
        source_value=text,
        source_origin=OriginType.PARAMETER,
    )
    with pytest.raises(UnicodeDecodeError):
        _ = get_tainted_ranges(value)[0].source.value
    add_secure_mark(value, [VulnerabilityType.UNVALIDATED_REDIRECT])
    added = "field added after the first callback"
    assert not is_pyobject_tainted(added)

    if structure_kind == "eager":
        result = taint_structure(
            {"location": value, "added": added},
            OriginType.PARAMETER_NAME,
            OriginType.PARAMETER,
            override_pyobject_tainted=True,
        )
        first, second = result["location"], result["added"]
    elif structure_kind == "lazy_dict":
        result = LazyTaintDict(
            {"location": value, "added": added},
            origins=(OriginType.PARAMETER_NAME, OriginType.PARAMETER),
            override_pyobject_tainted=True,
        )
        first, second = result["location"], result["added"]
    else:
        result = LazyTaintList(
            [value, added],
            origins=(OriginType.PARAMETER_NAME, OriginType.PARAMETER),
            override_pyobject_tainted=True,
            source_name="location",
        )
        first, second = result[0], result[1]

    # Traversal must still reach newly added fields after the undecodable source.
    assert is_pyobject_tainted(second)
    assert second == added
    assert first == value
    assert first is not value
    for item, name in ((first, "location"), (second, "location" if structure_kind == "lazy_list" else "added")):
        ranges = get_tainted_ranges(item)
        assert len(ranges) == 1
        assert ranges[0].start == 0
        assert ranges[0].length == len(item)
        assert ranges[0].source.origin == OriginType.PARAMETER
        assert ranges[0].source.name == name
        assert not ranges[0].has_secure_mark(VulnerabilityType.UNVALIDATED_REDIRECT)
