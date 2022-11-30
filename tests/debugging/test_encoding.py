# -*- coding: utf-8 -*-

import inspect
import json
import sys
import threading

import pytest

from ddtrace.debugging._encoding import BatchJsonEncoder
from ddtrace.debugging._encoding import SnapshotJsonEncoder
from ddtrace.debugging._encoding import _captured_context
from ddtrace.debugging._encoding import _get_args
from ddtrace.debugging._encoding import _get_fields
from ddtrace.debugging._encoding import _get_locals
from ddtrace.debugging._encoding import _serialize
from ddtrace.debugging._encoding import _serialize_exc_info
from ddtrace.debugging._probe.model import LineProbe
from ddtrace.debugging._snapshot.model import Snapshot
from ddtrace.internal._encoding import BufferFull
from ddtrace.internal.compat import PY2
from ddtrace.internal.compat import PY3


class Custom(object):
    def __init__(self):
        self.some_arg = ({"Hello": [None, 42, True, None, {b"World"}, 0.07]},)

    def somemethod(self):
        pass


class Node(object):
    def __init__(self, name, left=None, right=None):
        self.name = name
        self.left = left
        self.right = right

    def __repr__(self):
        return "Node(%s, %s, %s)" % (self.name, self.left, self.right)


class Tree(object):
    def __init__(self, name, root):
        self.name = name
        self.root = root


tree = Tree("root", Node("0", Node("0l", Node("0ll"), Node("0lr")), Node("0r", Node("0rl"))))


def test_get_args():
    def assert_args(args):
        assert set(dict(_get_args(inspect.currentframe().f_back)).keys()) == args

    def assert_locals(_locals):
        assert set(dict(_get_locals(inspect.currentframe().f_back)).keys()) == _locals

    def arg_and_kwargs(a, **kwargs):
        assert_args({"a", "kwargs"})
        assert_locals(set())

    def arg_and_args_and_kwargs(a, *ars, **kwars):
        assert_args({"a", "ars", "kwars"})
        assert_locals(set())

    def args_and_kwargs(*ars, **kwars):
        assert_args({"ars", "kwars"})
        assert_locals(set())

    def args(*ars):
        assert_args({"ars"})
        assert_locals(set())

    arg_and_kwargs(1, b=2)
    arg_and_args_and_kwargs(1, 42, b=2)
    args_and_kwargs()
    args()


@pytest.mark.parametrize(
    "value,serialized",
    [
        # Simple
        ("foo", "'foo'"),
        (10, "10"),
        (0.2, "0.2"),
        (True, "True"),
        (None, "None"),
        (b"Hello", "b'Hello'"[PY2:]),
        # Container
        ({"Hello": "World"}, "{'Hello': 'World'}"),
        ({"Hello": 42}, "{'Hello': 42}"),
        ({42: "World"}, "{42: 'World'}"),
        ({}, "{}"),
        (["Hello", 42, True, None, 10.0], "['Hello', 42, True, None, 10.0]"),
        ([], "[]"),
        (tuple(), "()"),
        ((None,), "(None)"),
        (set(), "set()"),
        ({0.1}, "{0.1}"),
        (
            ({"Hello": [None, 42, True, None, {b"World"}, 0.07]},),
            "({'Hello': [None, 42, True, None, {b'World'}, 0.07]})"
            if PY3
            else "({'Hello': [None, 42, True, None, {'World'}, 0.07]})",
        ),
    ],
)
def test_serialize(value, serialized):
    assert _serialize(value, level=-1) == serialized


def test_serialize_custom_object():

    assert _serialize(Custom(), level=-1) == (
        "Custom(some_arg=({'Hello': [None, 42, True, None, {b'World'}, 0.07]}))"
        if PY3
        else "Custom(some_arg=({'Hello': [None, 42, True, None, {'World'}, 0.07]}))"
    )

    q = "class" if PY3 else "type"
    assert _serialize(Custom()) == "Custom(some_arg=<%s 'tuple'>)" % q
    assert _serialize(Custom(), 2) == "Custom(some_arg=(<%s 'dict'>))" % q
    assert _serialize(Custom(), 3) == "Custom(some_arg=({'Hello': <%s 'list'>}))" % q
    assert _serialize(Custom(), 4) == "Custom(some_arg=({'Hello': [None, 42, True, None, <%s 'set'>, 0.07]}))" % q

    assert _serialize(Custom) == repr(Custom)


@pytest.mark.parametrize(
    "value,serialized",
    [
        (list(range(20)), "[" + ", ".join(map(str, range(10))) + ", ...]"),
        (tuple(range(20)), "(" + ", ".join(map(str, range(10))) + ", ...)"),
        (set(range(20)), "{" + ", ".join(map(str, range(10))) + ", ...}"),
        (list(range(10)), "[" + ", ".join(map(str, range(10))) + "]"),
        (tuple(range(10)), "(" + ", ".join(map(str, range(10))) + ")"),
        (set(range(10)), "{" + ", ".join(map(str, range(10))) + "}"),
    ],
)
def test_serialize_collection_max_size(value, serialized):
    assert _serialize(value) == serialized


def test_serialize_long_string():
    assert _serialize("x" * 11, max_str_len=10) == repr("x" * 9 + "...")


def test_serialize_exc_info():
    def a():
        raise ValueError("bad")

    def b():
        a()

    def c():
        b()

    serialized = None
    try:
        c()
    except ValueError:
        serialized = _serialize_exc_info(sys.exc_info())

    assert serialized is not None
    assert serialized["type"] == "ValueError"
    assert [_["function"] for _ in serialized["stacktrace"][:3]] == ["a", "b", "c"]
    assert serialized["message"] == "'bad'"


def test_captured_context_default_level():
    context = _captured_context([("self", tree)], [], (None, None, None))
    assert context["fields"]["root"]["fields"]["left"]["value"] == repr(Node)
    assert context["fields"]["root"]["fields"]["left"]["fields"] is None

    assert context["fields"]["root"]["fields"]["name"]["value"] == "'0'"
    assert "fields" not in context["fields"]["root"]["fields"]["name"]


def test_captured_context_two_level():
    context = _captured_context([("self", tree)], [], (None, None, None), 2)
    assert context["fields"]["root"]["fields"]["left"]["fields"]["right"]["value"] == repr(Node)
    assert context["fields"]["root"]["fields"]["left"]["fields"]["right"]["fields"] is None


def test_captured_context_three_level():
    context = _captured_context([("self", tree)], [], (None, None, None), 3)
    assert context["fields"]["root"]["fields"]["left"]["fields"]["right"]["fields"]["right"]["value"] == "None", context
    assert context["fields"]["root"]["fields"]["left"]["fields"]["right"]["fields"]["left"]["value"] == "None", context
    assert "fields" not in context["fields"]["root"]["fields"]["left"]["fields"]["right"]["fields"]["right"], context


def test_captured_context_exc():
    try:
        raise Exception("test", "me")
    except Exception:
        context = _captured_context([], [], sys.exc_info())
        exc = context.pop("throwable")
        assert context == {
            "fields": {},
            "arguments": {},
            "locals": {},
        }
        assert exc["message"] == "'test', 'me'"
        assert exc["type"] == "Exception"


def test_batch_json_encoder():
    s = Snapshot(
        LineProbe(probe_id="batch-test", source_file="foo.py", line=42),
        inspect.currentframe(),
        threading.current_thread(),
        sys.exc_info(),
        None,
    )

    # DEV: This local variable will appear in the snapshot and we are using it
    # to test that we can handle unicode strings.
    cake = "After the test there will be ✨ 🍰 ✨ in the annex"

    buffer_size = 30 * (1 << 10)
    encoder = BatchJsonEncoder({Snapshot: SnapshotJsonEncoder(None)}, buffer_size=buffer_size)
    snapshot_size = encoder.put(s)

    n_snapshots = buffer_size // snapshot_size
    for _ in range(n_snapshots - 1):
        encoder.put(s)

    with pytest.raises(BufferFull):
        encoder.put(s)

    count = encoder.count
    payload = encoder.encode()
    decoded = json.loads(payload.decode())
    assert len(decoded) == n_snapshots == count
    assert _serialize(cake) == decoded[0]["debugger.snapshot"]["captures"]["lines"]["42"]["locals"]["cake"]["value"]
    assert encoder.encode() is None
    assert encoder.encode() is None
    assert encoder.count == 0


def test_batch_flush_reencode():
    s = Snapshot(
        LineProbe(probe_id="batch-test", source_file="foo.py", line=42),
        inspect.currentframe(),
        threading.current_thread(),
        sys.exc_info(),
        None,
    )

    encoder = BatchJsonEncoder({Snapshot: SnapshotJsonEncoder(None)})

    snapshot_total_size = sum(encoder.put(s) for _ in range(2))
    assert encoder.count == 2
    assert len(encoder.encode()) == snapshot_total_size + 3

    a, b = encoder.put(s), encoder.put(s)
    assert abs(a - b) < 1024
    assert encoder.count == 2
    assert len(encoder.encode()) == a + b + 3


# ---- Side effects ----


class SideEffects(object):
    class SideEffect(Exception):
        pass

    def __getattribute__(self, name):
        raise SideEffects.SideEffect()

    def __get__(self, instance, owner):
        raise self.SideEffect()

    @property
    def property_with_side_effect(self):
        raise self.SideEffect()


def test_serialize_side_effects():
    assert _serialize(SideEffects()) == "SideEffects()"


def test_get_fields_side_effects():
    assert _get_fields(SideEffects()) == {}


# ---- Slots ----


def test_get_fields_slots():
    class A(object):
        __slots__ = ["a"]

        def __init__(self):
            self.a = "a"

    class B(A):
        __slots__ = ["b"]

        def __init__(self):
            super(B, self).__init__()
            self.b = "b"

    assert _get_fields(A()) == {"a": "a"}
    assert _get_fields(B()) == {"a": "a", "b": "b"}
