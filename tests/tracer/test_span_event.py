import pickle
import time

import pytest

from ddtrace.internal.native._native import SpanEvent


def test_construction_full():
    event = SpanEvent("myevent", {"k": "v"}, 12345)
    assert event.name == "myevent"
    assert event.attributes == {"k": "v"}
    assert event.time_unix_nano == 12345


def test_defaults():
    before_ns = time.time_ns()
    event = SpanEvent("myevent")
    after_ns = time.time_ns()
    assert event.name == "myevent"
    assert event.attributes == {}
    assert before_ns <= event.time_unix_nano <= after_ns


def test_none_attributes_defaults_to_empty():
    event = SpanEvent("myevent", None, 12345)
    assert event.attributes == {}


def test_negative_time_unix_nano_defaults_to_zero():
    event = SpanEvent("myevent", {}, -1)
    assert event.time_unix_nano == 0


def test_zero_time_unix_nano_accepted():
    event = SpanEvent("myevent", {}, 0)
    assert event.time_unix_nano == 0


def test_invalid_type_time_unix_nano_defaults_to_zero():
    event = SpanEvent("myevent", {}, "not-a-number")
    assert event.time_unix_nano == 0


def test_mapping_attributes_accepted():
    from types import MappingProxyType

    proxy = MappingProxyType({"k": "v"})
    event = SpanEvent("myevent", proxy, 12345)
    assert event.attributes == {"k": "v"}


def test_import_from_native_module():
    from ddtrace.internal.native._native import SpanEvent as SE  # noqa: F401


def test_frozen_name():
    event = SpanEvent("myevent")
    with pytest.raises(AttributeError):
        event.name = "other"


def test_frozen_time_unix_nano():
    event = SpanEvent("myevent", {}, 12345)
    with pytest.raises(AttributeError):
        event.time_unix_nano = 99999


def test_frozen_attributes():
    event = SpanEvent("myevent", {}, 12345)
    with pytest.raises(AttributeError):
        event.attributes = {"new": "val"}


def test_frozen_no_new_attributes():
    event = SpanEvent("myevent")
    with pytest.raises(AttributeError):
        event.extra = "oops"


def test_iter_with_attributes():
    event = SpanEvent("myevent", {"k": "v"}, 12345)
    d = dict(event)
    assert d == {"name": "myevent", "time_unix_nano": 12345, "attributes": {"k": "v"}}


def test_iter_empty_attributes_omitted():
    event = SpanEvent("myevent", {}, 12345)
    d = dict(event)
    assert d == {"name": "myevent", "time_unix_nano": 12345}
    assert "attributes" not in d


def test_iter_none_attributes_omitted():
    event = SpanEvent("myevent", None, 12345)
    d = dict(event)
    assert "attributes" not in d


def test_repr_with_attributes():
    event = SpanEvent("myevent", {"k": "v"}, 12345)
    assert repr(event) == "SpanEvent(name='myevent', time=12345, attributes={'k': 'v'})"


def test_repr_without_attributes():
    event = SpanEvent("myevent", {}, 12345)
    assert repr(event) == "SpanEvent(name='myevent', time=12345, attributes={})"


def test_pickle_roundtrip():
    event = SpanEvent("myevent", {"k": "v"}, 12345)
    restored = pickle.loads(pickle.dumps(event))
    assert restored.name == event.name
    assert restored.attributes == event.attributes
    assert restored.time_unix_nano == event.time_unix_nano


def test_pickle_roundtrip_empty_attributes():
    event = SpanEvent("myevent", {}, 99999)
    restored = pickle.loads(pickle.dumps(event))
    assert restored.name == "myevent"
    assert restored.attributes == {}
    assert restored.time_unix_nano == 99999
