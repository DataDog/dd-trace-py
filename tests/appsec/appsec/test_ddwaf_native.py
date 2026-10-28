"""Parity and lifetime tests for the native (Rust) libddwaf backend.

The ctypes serializer and backend are the reference: native encoding must produce the same raw
native values (type tags and exact bytes) and truncation reports, and evaluations must reach the
same security decisions.
"""

from collections import UserDict
from collections import UserList
from concurrent.futures import ThreadPoolExecutor
import ctypes
import gc
import json
import os
import struct
import subprocess
import sys
import uuid

from hypothesis import HealthCheck
from hypothesis import given
from hypothesis import settings
from hypothesis import strategies as st
import pytest

from ddtrace.appsec._constants import DEFAULT
from ddtrace.appsec._ddwaf import ddwaf_types as ctypes_types
from ddtrace.appsec._ddwaf import waf as native_waf
from ddtrace.appsec._ddwaf import waf_ctypes
from ddtrace.internal.native._native import ddwaf as native
import tests.appsec.rules as rules


def ctypes_view(obj):
    """Raw view of a ctypes-built object, in the shape returned by native._encode."""
    tags = ctypes_types.DDWAF_OBJ_TYPE
    tag = obj.type
    if tag == tags.DDWAF_OBJ_SMALL_STRING:
        value = obj.value.sstr
        return (
            "string",
            ctypes.string_at(ctypes.addressof(value) + ctypes_types._SMALL_STRING_DATA_OFFSET, value.size),
        )
    if tag in (tags.DDWAF_OBJ_STRING, tags.DDWAF_OBJ_LITERAL_STRING):
        value = obj.value.str
        return ("string", ctypes.string_at(value.ptr, value.size) if value.size else b"")
    if tag == tags.DDWAF_OBJ_ARRAY:
        value = obj.value.array
        return ("array", [ctypes_view(value.ptr[i]) for i in range(value.size)])
    if tag == tags.DDWAF_OBJ_MAP:
        value = obj.value.map
        return ("map", [(ctypes_view(value.ptr[i].key), ctypes_view(value.ptr[i].val)) for i in range(value.size)])
    if tag == tags.DDWAF_OBJ_SIGNED:
        return ("signed", obj.value.i64.val)
    if tag == tags.DDWAF_OBJ_UNSIGNED:
        return ("unsigned", obj.value.u64.val)
    if tag == tags.DDWAF_OBJ_BOOL:
        return ("bool", obj.value.b8.val)
    if tag == tags.DDWAF_OBJ_FLOAT:
        return ("float", struct.unpack("<Q", struct.pack("<d", obj.value.f64.val))[0])
    return ("null",) if tag == tags.DDWAF_OBJ_NULL else ("invalid",)


def ctypes_encode(value, **limits):
    observator = ctypes_types._observator()
    obj = ctypes_types.ddwaf_object(value, observator=observator, **limits)
    try:
        return ctypes_view(obj), (observator.string_length, observator.container_size, observator.container_depth)
    finally:
        ctypes_types.ddwaf_object_free(obj)


def assert_same_encoding(value, **limits):
    assert native._encode(value, **limits) == ctypes_encode(value, **limits)


class _Str:
    def __str__(self):
        return "custom-object-" * 2


cycle = []
cycle.append(cycle)
deep = [1]
for _ in range(10):
    deep = [deep, {"k": deep}]

CASES = [
    None,
    False,
    True,
    0,
    -1,
    2**63 - 1,
    2**64 - 1,
    2**80,
    -(2**70),
    0.0,
    -0.0,
    float("nan"),
    float("inf"),
    "",
    b"",
    "a\x00b",
    b"\xff\x00a",
    "bad\ud800text",
    "\udfff" * 20,
    "é" * 7,
    "é" * 8,
    "東京" * 3,
    "🦆" * 5,
    "🦆" * 2048,
    "x" * 4097,
    "é" * 4097,
    b"\xff" * 5000,
    {"x" * 15: "a" * 15},
    {"城市": "東京", "emoji": "🦆" * 16, "names": ["café", "naïve", "élève"]},
    {b"raw\x00key": b"\xff\x00value" * 32, "blob": b"x" * 1024},
    {1: "ignored", None: "ignored", b"key": 2, "key": 3, "": "empty", 2.5: "ignored"},
    UserDict({"x": UserList([True, 2, "three"])}),
    {"tuple": (1, 2), "range": range(5), "bytearray": bytearray(b"abc"), "set": {1}, "frozen": frozenset()},
    _Str(),
    type("S", (str,), {})("str-subclass"),
    type("I", (int,), {})(42),
    type("D", (dict,), {})({"k": "v"}),
    list(range(300)),
    {str(i): i for i in range(300)},
    deep,
    cycle,
    {"long": "🦆" * 2048, "values": list(range(300))},
]


@pytest.mark.parametrize("value", CASES, ids=range(len(CASES)))
@pytest.mark.parametrize(
    "limits",
    [
        {},
        {"max_objects": 1, "max_depth": 1, "max_string_length": 2},
        {"max_objects": 0},
        {"max_depth": 0},
        {"max_string_length": 0},
        {"max_string_length": 14},
        {"max_string_length": 15},
        {"max_objects": 2**31, "max_depth": 1000, "max_string_length": 2**31},
    ],
)
def test_encoding_matches_ctypes(value, limits):
    try:
        expected = ctypes_encode(value, **limits)
    except (RecursionError, ctypes.ArgumentError):
        # The Python serializer cannot reach the unlimited depth of a cycle; the native one can.
        native._encode(value, **limits)
        return
    assert native._encode(value, **limits) == expected


@pytest.mark.parametrize("container", ["list", "dict"])
def test_uint16_capacity_cap_matches_ctypes(container):
    n = ctypes_types.DDWAF_OBJ_MAX_CAPACITY + 10
    value = list(range(n)) if container == "list" else {str(i): i for i in range(n)}
    assert_same_encoding(value, max_objects=2**31)


SCALARS = st.one_of(st.none(), st.booleans(), st.integers(), st.floats(), st.text(), st.binary(), st.characters())
VALUES = st.recursive(
    SCALARS,
    lambda inner: st.lists(inner) | st.tuples(inner, inner) | st.dictionaries(SCALARS | st.text(), inner),
    max_leaves=40,
)


@settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large])
@given(
    value=VALUES,
    max_objects=st.sampled_from([0, 1, 4, 256]),
    max_depth=st.sampled_from([0, 1, 3, 20]),
    max_string_length=st.sampled_from([0, 2, 14, 15, 4096]),
)
def test_generated_encoding_matches_ctypes(value, max_objects, max_depth, max_string_length):
    assert_same_encoding(value, max_objects=max_objects, max_depth=max_depth, max_string_length=max_string_length)


def test_conversion_errors_propagate():
    class Boom:
        def __str__(self):
            raise ZeroDivisionError("boom")

    with pytest.raises(ZeroDivisionError):
        native._encode({"a": ["x" * 100, {"b": Boom()}]})


def test_dict_mutation_during_conversion_raises():
    data = {}

    class Mutator:
        def __str__(self):
            data["new"] = "value"
            return "value"

    data["original"] = Mutator()
    data["other"] = 1
    with pytest.raises(RuntimeError, match="changed size"):
        native._encode(data)


def _normalize(events, actions):
    events, actions = json.loads(json.dumps([events, actions]))
    ids = {}

    def norm(value):
        uuid.UUID(value)
        return ids.setdefault(value, f"response-{len(ids)}")

    for event in events:
        if "security_response_id" in event:
            event["security_response_id"] = norm(event["security_response_id"])
    for params in actions.values():
        if isinstance(params, dict) and "security_response_id" in params:
            params["security_response_id"] = norm(params["security_response_id"])
    return events, actions


def _observe(result):
    events, actions = _normalize(result.data, result.actions)
    trunc = result.truncation
    return (
        result.return_code,
        events,
        actions,
        result.keep,
        result.timeout,
        (trunc.string_length, trunc.container_size, trunc.container_depth),
        result.meta_tags,
        result.metrics,
        result.api_security,
    )


REQUESTS = {
    "benign": [{"server.request.query": {"q": "hello"}}],
    "sqli": [{"server.request.cookies": {"attack": "1' or '1' = '1'"}}],
    "blocking_ip": [{"http.client_ip": "8.8.4.4"}],
    "staged": [
        {"server.request.headers.no_cookies": {"host": "example.test", "user-agent": "werkzeug/2.1.2"}},
        {"server.request.body": {"q": "1' or '1' = '1'"}},
    ],
    "truncated": [{"server.request.body": {"long": "🦆" * 2048, "values": list(range(300))}}],
    "api_security": [
        {
            "server.request.headers.no_cookies": {"content-type": "application/json"},
            "server.request.body": {"a": [1, 2, {"b": "c"}]},
            "waf.context.processor": {"extract-schema": True},
        }
    ],
}


@pytest.mark.parametrize("rules_path", [rules.RULES_GOOD_PATH, DEFAULT.RULES])
@pytest.mark.parametrize("name", list(REQUESTS))
def test_evaluation_matches_ctypes(rules_path, name):
    with open(rules_path, "rb") as f:
        ruleset = f.read()
    observed = []
    for backend in (native_waf, waf_ctypes):
        waf = backend.DDWaf(ruleset, b"", b"")
        assert waf.initialized
        ctx = waf._at_request_start()
        observed.append([_observe(waf.run(ctx, data, timeout_ms=1000)) for data in REQUESTS[name]])
    assert observed[0] == observed[1]


def test_info_matches_ctypes():
    for path in (rules.RULES_GOOD_PATH, rules.ROOT_DIR + "/rules-with-2-errors.json"):
        with open(path, "rb") as f:
            ruleset = f.read()
        infos = [backend.DDWaf(ruleset, b"", b"").info for backend in (native_waf, waf_ctypes)]
        assert repr(infos[0]) == repr(infos[1])
        assert sorted(native_waf.DDWaf(ruleset, b"", b"").required_data) == sorted(
            waf_ctypes.DDWaf(ruleset, b"", b"").required_data
        )


@pytest.fixture
def waf():
    with open(rules.RULES_GOOD_PATH, "rb") as f:
        return native_waf.DDWaf(f.read(), b"", b"")


SQLI = {"server.request.cookies": {"attack": "1' or '1' = '1'"}}


def test_subcontext_data_does_not_persist(waf):
    ctx = waf._at_request_start()
    sub = waf.new_subcontext(ctx)
    assert waf.run(sub, SQLI).data
    # The parent never saw the subcontext data, so it can still match it.
    assert waf.run(ctx, SQLI).data


def test_subcontext_outlives_parent(waf):
    ctx = waf._at_request_start()
    assert not waf.run(ctx, {"server.request.query": {"q": "hello"}}).data
    sub = waf.new_subcontext(ctx)
    del ctx
    gc.collect()
    assert waf.run(sub, SQLI).data


def test_context_survives_rules_update(waf):
    ctx = waf._at_request_start()
    replacement = {
        "version": "2.2",
        "rules": [
            {
                "id": "replacement",
                "name": "replacement",
                "tags": {"type": "test", "category": "test"},
                "conditions": [
                    {
                        "operator": "match_regex",
                        "parameters": {"inputs": [{"address": "server.request.query"}], "regex": "replacement"},
                    }
                ],
            }
        ],
    }
    assert waf.update_rules([], [("ASM_DD", "ASM_DD/replacement", replacement)])
    gc.collect()
    # The old context keeps its ruleset generation.
    assert waf.run(ctx, SQLI).data
    new_ctx = waf._at_request_start()
    assert not waf.run(new_ctx, SQLI).data
    assert waf.run(new_ctx, {"server.request.query": {"q": "replacement"}}).data
    assert waf.update_rules([("ASM_DD", "ASM_DD/replacement")], [])
    assert waf.run(waf._at_request_start(), SQLI).data


def test_nested_subcontext_rejected(waf):
    sub = waf.new_subcontext(waf._at_request_start())
    with pytest.raises(ValueError):
        sub.native.subcontext()


def test_persistent_data_matches_once(waf):
    ctx = waf._at_request_start()
    assert waf.run(ctx, SQLI).data
    assert not waf.run(ctx, {}).data


def test_concurrent_runs_on_shared_context(waf):
    ctx = waf._at_request_start()
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(
            executor.map(lambda i: bool(waf.run(ctx, {"server.request.query": {"q": str(i)}}).data), range(64))
        )
    assert not any(results)
    with ThreadPoolExecutor(max_workers=8) as executor:
        matches = list(executor.map(lambda _: bool(waf.run(waf.new_subcontext(ctx), SQLI).data), range(64)))
    assert all(matches)


def test_invalid_ruleset_json():
    with pytest.raises(ValueError):
        native_waf.DDWaf(b"not json", b"", b"")


def test_registration_does_not_load_and_load_failures_are_typed():
    script = """
import ctypes.util, sys
from ddtrace.internal.native._native import ddwaf
try:
    ddwaf.Builder()
except RuntimeError as e:
    assert "not loaded" in str(e)
else:
    raise AssertionError("builder before load")
try:
    ddwaf.load(["/nonexistent/libddwaf.so"])
except OSError:
    pass
else:
    raise AssertionError("missing library loaded")
libc = ctypes.util.find_library("c") or "libc.so.6"
try:
    ddwaf.load(["/nonexistent/libddwaf.so", libc])
except OSError as e:
    assert "missing required symbol" in str(e) or "could not load" in str(e), e
else:
    raise AssertionError("libc accepted as libddwaf")
"""
    subprocess.run([sys.executable, "-c", script], check=True, timeout=60)


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires os.fork")
def test_fork_ownership():
    script = """
import gc, json, os
from ddtrace.appsec._ddwaf import waf
rules = json.dumps({
    "version": "2.2",
    "rules": [{
        "id": "r1",
        "name": "r1",
        "tags": {"type": "t", "category": "c"},
        "conditions": [{"operator": "match_regex", "parameters": {"inputs": [{"address": "q"}], "regex": "attack"}}],
    }],
}).encode()
inst = waf.DDWaf(rules, b"", b"")
ctx = inst._at_request_start()
pid = os.fork()
if pid == 0:
    try:
        for fn in (ctx.native.subcontext, lambda: ctx.run({}, 1000)):
            try:
                fn()
            except RuntimeError as e:
                assert "after fork" in str(e)
            else:
                raise AssertionError("inherited request context allowed after fork")
        del ctx
        gc.collect()
        # Prefork workers inherit the compiled DDWaf handle and builder state.
        child_ctx = inst._at_request_start()
        assert inst.run(child_ctx, {"q": "attack"}).data
        assert inst.update_rules([], [("ASM_DD", "ASM_DD/child", json.loads(rules))])
        assert inst.run(inst._at_request_start(), {"q": "attack"}).data
    except BaseException:
        import traceback
        traceback.print_exc()
        os._exit(1)
    os._exit(0)
_, status = os.waitpid(pid, 0)
assert os.waitstatus_to_exitcode(status) == 0
"""
    subprocess.run([sys.executable, "-c", script], check=True, timeout=60)
