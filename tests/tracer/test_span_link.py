"""
Tests for SpanLink after migration to a frozen Rust PyO3 class.
"""

import pickle
from types import MappingProxyType

import pytest

from ddtrace._trace._span_link import SpanLink
from ddtrace._trace._span_pointer import _SpanPointerDirection


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_construction_all_args():
    link = SpanLink(
        trace_id=1,
        span_id=2,
        tracestate="ts",
        flags=1,
        attributes={"k": "v"},
    )
    assert link.trace_id == 1
    assert link.span_id == 2
    assert link.tracestate == "ts"
    assert link.flags == 1
    assert link.attributes == {"k": "v"}


def test_construction_minimal():
    link = SpanLink(trace_id=1, span_id=2)
    assert link.trace_id == 1
    assert link.span_id == 2
    assert link.tracestate is None
    assert link.flags is None
    assert link.attributes == {}


def test_none_attributes_defaults_to_empty():
    link = SpanLink(trace_id=1, span_id=2, attributes=None)
    assert link.attributes == {}


def test_mapping_attributes_accepted():
    attrs = MappingProxyType({"k": "v"})
    link = SpanLink(trace_id=1, span_id=2, attributes=attrs)
    assert link.attributes == {"k": "v"}
    assert isinstance(link.attributes, dict)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_validation_trace_id_zero():
    with pytest.raises(ValueError) as exc_info:
        SpanLink(trace_id=0, span_id=1)
    assert str(exc_info.value) == "trace_id must be > 0. Value is 0"


def test_validation_span_id_zero():
    with pytest.raises(ValueError) as exc_info:
        SpanLink(trace_id=1, span_id=0)
    assert str(exc_info.value) == "span_id must be > 0. Value is 0"


def test_skip_validation():
    link = SpanLink(trace_id=0, span_id=0, _skip_validation=True)
    assert link.trace_id == 0
    assert link.span_id == 0


# ---------------------------------------------------------------------------
# Frozen / immutable
# ---------------------------------------------------------------------------


def _make_link():
    return SpanLink(trace_id=1, span_id=2, tracestate="ts", flags=1, attributes={"k": "v"})


def test_frozen_trace_id():
    link = _make_link()
    with pytest.raises(AttributeError):
        link.trace_id = 99


def test_frozen_span_id():
    link = _make_link()
    with pytest.raises(AttributeError):
        link.span_id = 99


def test_frozen_tracestate():
    link = _make_link()
    with pytest.raises(AttributeError):
        link.tracestate = "other"


def test_frozen_flags():
    link = _make_link()
    with pytest.raises(AttributeError):
        link.flags = 99


def test_frozen_attributes():
    link = _make_link()
    with pytest.raises(AttributeError):
        link.attributes = {}


def test_frozen_no_new_attrs():
    link = _make_link()
    with pytest.raises(AttributeError):
        link.new_field = "x"


def test_attributes_dict_mutable():
    link = _make_link()
    link.attributes["new"] = "val"
    assert link.attributes["new"] == "val"


# ---------------------------------------------------------------------------
# to_dict
# ---------------------------------------------------------------------------


def test_to_dict_basic():
    link = SpanLink(trace_id=1, span_id=2)
    d = link.to_dict()
    assert d["trace_id"] == "00000000000000000000000000000001"
    assert d["span_id"] == "0000000000000002"
    assert len(d["trace_id"]) == 32
    assert len(d["span_id"]) == 16


def test_to_dict_with_attributes():
    link = SpanLink(trace_id=1, span_id=2, attributes={"key": [1, 2], "flag": True})
    d = link.to_dict()
    attrs = d["attributes"]
    # Lists get flattened with dot-indexed keys
    assert attrs["key.0"] == "1"
    assert attrs["key.1"] == "2"
    # Bools are lowercase strings
    assert attrs["flag"] == "true"


def test_to_dict_with_tracestate_and_flags():
    link = SpanLink(trace_id=1, span_id=2, tracestate="vendor=value", flags=1)
    d = link.to_dict()
    assert d["tracestate"] == "vendor=value"
    assert d["flags"] == 1


def test_to_dict_empty_attributes_omitted():
    link = SpanLink(trace_id=1, span_id=2)
    d = link.to_dict()
    assert "attributes" not in d


def test_to_dict_flags_none_omitted():
    # flags=None must not appear in to_dict() output
    link = SpanLink(trace_id=1, span_id=2)
    assert "flags" not in link.to_dict()


def test_to_dict_flags_zero_present():
    # flags=0 is distinct from flags=None and must appear in to_dict() output
    link = SpanLink(trace_id=1, span_id=2, flags=0)
    assert link.to_dict()["flags"] == 0


# ---------------------------------------------------------------------------
# Equality
# ---------------------------------------------------------------------------


def test_eq_same():
    a = SpanLink(trace_id=1, span_id=2, tracestate="ts", flags=1, attributes={"k": "v"})
    b = SpanLink(trace_id=1, span_id=2, tracestate="ts", flags=1, attributes={"k": "v"})
    assert a == b


def test_eq_different():
    a = SpanLink(trace_id=1, span_id=2)
    b = SpanLink(trace_id=1, span_id=3)
    assert a != b


def test_eq_different_type():
    a = SpanLink(trace_id=1, span_id=2)
    assert a != "not a link"


# ---------------------------------------------------------------------------
# Repr
# ---------------------------------------------------------------------------


def test_repr():
    link = SpanLink(trace_id=1, span_id=2, tracestate="ts", flags=1, attributes={"k": "v"})
    r = repr(link)
    assert r.startswith("SpanLink(")
    assert "trace_id=1" in r
    assert "span_id=2" in r
    assert "attributes={'k': 'v'}" in r
    assert "tracestate=ts" in r
    assert "flags=1" in r


# ---------------------------------------------------------------------------
# Pickle
# ---------------------------------------------------------------------------


def test_pickle_roundtrip():
    link = SpanLink(trace_id=1, span_id=2, tracestate="ts", flags=1, attributes={"k": "v"})
    restored = pickle.loads(pickle.dumps(link))
    assert restored.trace_id == link.trace_id
    assert restored.span_id == link.span_id
    assert restored.tracestate == link.tracestate
    assert restored.flags == link.flags
    assert restored.attributes == link.attributes


def test_pickle_roundtrip_defaults():
    link = SpanLink(trace_id=1, span_id=2)
    restored = pickle.loads(pickle.dumps(link))
    assert restored.trace_id == 1
    assert restored.span_id == 2
    assert restored.tracestate is None
    assert restored.flags is None
    assert restored.attributes == {}


def test_pickle_roundtrip_skip_validation():
    link = SpanLink(trace_id=0, span_id=0, _skip_validation=True)
    restored = pickle.loads(pickle.dumps(link))
    assert restored.trace_id == 0
    assert restored.span_id == 0


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


def test_import_path():
    from ddtrace._trace._span_link import SpanLink as SL

    assert SL is SpanLink


# ---------------------------------------------------------------------------
# Span pointer construction
# ---------------------------------------------------------------------------


def test_span_pointer_construction():
    link = SpanLink(
        trace_id=0,
        span_id=0,
        attributes={
            "link.kind": "span-pointer",
            "ptr.kind": "pk",
            "ptr.dir": _SpanPointerDirection.UPSTREAM.value,
            "ptr.hash": "abc123",
        },
        _skip_validation=True,
    )
    assert link.trace_id == 0
    assert link.span_id == 0
    assert link.attributes["link.kind"] == "span-pointer"
    assert link.attributes["ptr.kind"] == "pk"
    assert link.attributes["ptr.dir"] == _SpanPointerDirection.UPSTREAM.value
    assert link.attributes["ptr.hash"] == "abc123"


# ---------------------------------------------------------------------------
# flatten_key_value (native)
# ---------------------------------------------------------------------------


def test_flatten_key_value_non_sequence():
    from ddtrace.internal.native._native import flatten_key_value

    result = flatten_key_value("key", "val")
    assert result == {"key": "val"}


def test_flatten_key_value_list():
    from ddtrace.internal.native._native import flatten_key_value

    result = flatten_key_value("key", [1, 2, 3])
    assert result == {"key.0": 1, "key.1": 2, "key.2": 3}


def test_flatten_key_value_nested():
    from ddtrace.internal.native._native import flatten_key_value

    result = flatten_key_value("key", [1, [2, 3]])
    assert result == {"key.0": 1, "key.1.0": 2, "key.1.1": 3}
