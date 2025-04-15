import json

from hypothesis import given
from hypothesis import strategies as st
import pytest

from ddtrace.appsec import _metrics
import ddtrace.appsec._constants as constants
import ddtrace.appsec._ddwaf as ddwaf


def build_schema(obj):
    rules = {}
    with open(constants.DEFAULT.RULES, "r") as f_apisec:
        rules.update(json.load(f_apisec))
    waf = ddwaf.DDWaf(rules, b"", b"", _metrics)
    ctx = waf._at_request_start()
    res = waf.run(
        ctx,
        {"server.request.body": obj, constants.WAF_DATA_NAMES.PROCESSOR_SETTINGS: {"extract-schema": True}},
        timeout_ms=50_000.0,
    ).derivatives
    return res["_dd.appsec.s.req.body"]


SCALAR_OBJECTS = st.one_of(st.none(), st.booleans(), st.integers(), st.floats(), st.characters())

PYTHON_OBJECTS = st.recursive(
    base=SCALAR_OBJECTS,
    extend=lambda inner: st.lists(inner) | st.dictionaries(SCALAR_OBJECTS, inner),
)


@given(obj=PYTHON_OBJECTS)
def test_build_schema(obj):
    obj = build_schema(obj)
    repr(obj)
    del obj


def equal_with_meta(t1, t2):
    if t1 is None or t2 is None:
        return False
    meta1 = t1[1] if len(t1) > 1 else {}
    meta2 = t2[1] if len(t2) > 1 else {}
    return meta1 == meta2 and equal_value(t1[0], t2[0])


def equal_value(t1, t2):
    if isinstance(t1, list) and isinstance(t2, list):
        return len(t1) == len(t2) and all(any(equal_with_meta(a, b) for b in t2) for a in t1)
    if isinstance(t1, dict) and isinstance(t2, dict):
        return len(t1) == len(t2) and all(equal_with_meta(t1[k], t2.get(k)) for k in t1)
    return t1 == t2


@pytest.mark.parametrize(
    "obj, res",
    [
        (32, [4]),
        (True, [2]),
        ("test", [8]),
        (b"test", [8]),
        (1.0, [16]),
        ([1, 2], [[[4]], {"len": 2}]),
        ({"test": "truc"}, [{"test": [8]}]),
        (None, [1]),
    ],
)
def test_small_schemas(obj, res):
    assert equal_with_meta(build_schema(obj), res)


def deep_build(n, mini=0):
    if n <= mini:
        return {}
    return {str(n): deep_build(n - 1, mini)}


def deep_build_schema(n, mini=0):
    if n <= mini:
        return [{}]
    return [{str(n): deep_build_schema(n - 1, mini)}]


@pytest.mark.parametrize(
    "obj, res",
    [
        (324, [4]),
        (True, [2]),
        (
            [[3] * i for i in range(20)],
            [
                [
                    [[], {"len": 0}],
                    [[[4]], {"len": 1}],
                    [[[4]], {"len": 2}],
                    [[[4]], {"len": 3}],
                    [[[4]], {"len": 4}],
                    [[[4]], {"len": 5}],
                    [[[4]], {"len": 6}],
                    [[[4]], {"len": 7}],
                    [[[4]], {"len": 8}],
                    [[[4]], {"len": 9}],
                ],
                {"len": 20, "truncated": True},
            ],
        ),
        ({str(i): "toast" for i in range((512))}, [{str(i): [8] for i in range(255)}, {"truncated": True}]),
        (deep_build(40), deep_build_schema(40, 23)),
    ],
)
def test_limits(obj, res):
    schema = build_schema(obj)
    assert equal_with_meta(schema, res)  # max_depth=18, max_girth=255, max_types_in_array=10


@pytest.mark.parametrize(
    "obj, res",
    [
        ({"US PASSPORT": "C03005988"}, [{"US PASSPORT": [8, {"category": "pii", "type": "passport_number"}]}]),
        ({"ViN": "1HGBH41JXMN109186"}, [{"ViN": [8, {"category": "pii", "type": "vin"}]}]),
    ],
)
def test_scanners(obj, res):
    schema = build_schema(obj)
    assert equal_with_meta(schema, res)  # max_depth=18, max_girth=255, max_types_in_array=10
