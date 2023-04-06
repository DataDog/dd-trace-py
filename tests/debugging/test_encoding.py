# -*- coding: utf-8 -*-

import inspect
import json
import sys
import threading

import pytest

from ddtrace.debugging._capture import utils
from ddtrace.debugging._capture.snapshot import Snapshot
from ddtrace.debugging._encoding import BatchJsonEncoder
from ddtrace.debugging._encoding import SnapshotJsonEncoder
from ddtrace.debugging._encoding import _capture_context
from ddtrace.debugging._encoding import format_message
from ddtrace.debugging._probe.model import CaptureLimits
from ddtrace.debugging._probe.model import MAXSIZE
from ddtrace.internal._encoding import BufferFull
from ddtrace.internal.compat import PY2
from ddtrace.internal.compat import PY3
from tests.debugging.test_safety import SideEffects
from tests.debugging.utils import create_snapshot_line_probe


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
    assert utils.serialize(value, level=-1) == serialized


def test_serialize_custom_object():

    assert utils.serialize(Custom(), level=-1) == (
        "Custom(some_arg=({'Hello': [None, 42, True, None, {b'World'}, 0.07]}))"
        if PY3
        else "Custom(some_arg=({'Hello': [None, 42, True, None, {'World'}, 0.07]}))"
    )

    q = "class" if PY3 else "type"
    assert utils.serialize(Custom(), 1) == "Custom(some_arg=<%s 'tuple'>)" % q
    assert utils.serialize(Custom(), 2) == "Custom(some_arg=(<%s 'dict'>))" % q
    assert utils.serialize(Custom(), 3) == "Custom(some_arg=({'Hello': <%s 'list'>}))" % q
    assert utils.serialize(Custom(), 4) == "Custom(some_arg=({'Hello': [None, 42, True, None, <%s 'set'>, 0.07]}))" % q

    assert utils.serialize(Custom) == repr(Custom)


@pytest.mark.parametrize(
    "value,serialized",
    [
        (list(range(MAXSIZE << 1)), "[" + ", ".join(map(str, range(MAXSIZE))) + ", ...]"),
        (tuple(range(MAXSIZE << 1)), "(" + ", ".join(map(str, range(MAXSIZE))) + ", ...)"),
        (set(range(MAXSIZE << 1)), "{" + ", ".join(map(str, range(MAXSIZE))) + ", ...}"),
        (list(range(MAXSIZE)), "[" + ", ".join(map(str, range(MAXSIZE))) + "]"),
        (tuple(range(MAXSIZE)), "(" + ", ".join(map(str, range(MAXSIZE))) + ")"),
        (set(range(MAXSIZE)), "{" + ", ".join(map(str, range(MAXSIZE))) + "}"),
    ],
)
def test_serialize_collection_max_size(value, serialized):
    assert utils.serialize(value) == serialized


def test_serialize_long_string():
    assert utils.serialize("x" * 11, maxlen=10) == repr("x" * 9 + "...")


def test_capture_exc_info():
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
        serialized = utils.capture_exc_info(sys.exc_info())

    assert serialized is not None
    assert serialized["type"] == "ValueError"
    assert [_["function"] for _ in serialized["stacktrace"][:3]] == ["a", "b", "c"]
    assert serialized["message"] == "'bad'"


def test_capture_context_default_level():
    context = _capture_context([("self", tree)], [], (None, None, None), CaptureLimits(max_level=0))
    self = context["arguments"]["self"]
    assert self["fields"]["root"]["notCapturedReason"] == "depth"


def test_capture_context_one_level():
    context = _capture_context([("self", tree)], [], (None, None, None), CaptureLimits(max_level=1))
    self = context["arguments"]["self"]

    assert self["fields"]["root"]["fields"]["left"] == {"notCapturedReason": "depth", "type": "Node"}

    assert self["fields"]["root"]["fields"]["name"]["value"] == "'0'"
    assert "fields" not in self["fields"]["root"]["fields"]["name"]


def test_capture_context_two_level():
    context = _capture_context([("self", tree)], [], (None, None, None), CaptureLimits(max_level=2))
    self = context["arguments"]["self"]
    assert self["fields"]["root"]["fields"]["left"]["fields"]["right"] == {"notCapturedReason": "depth", "type": "Node"}


def test_capture_context_three_level():
    context = _capture_context([("self", tree)], [], (None, None, None), CaptureLimits(max_level=3))
    self = context["arguments"]["self"]
    assert self["fields"]["root"]["fields"]["left"]["fields"]["right"]["fields"]["right"]["isNull"], context
    assert self["fields"]["root"]["fields"]["left"]["fields"]["right"]["fields"]["left"]["isNull"], context
    assert "fields" not in self["fields"]["root"]["fields"]["left"]["fields"]["right"]["fields"]["right"], context


def test_capture_context_exc():
    try:
        raise Exception("test", "me")
    except Exception:
        context = _capture_context([], [], sys.exc_info())
        exc = context.pop("throwable")
        assert context == {
            "arguments": {},
            "locals": {},
        }
        assert exc["message"] == "'test', 'me'"
        assert exc["type"] == "Exception"


def test_batch_json_encoder():
    s = Snapshot(
        probe=create_snapshot_line_probe(probe_id="batch-test", source_file="foo.py", line=42),
        frame=inspect.currentframe(),
        thread=threading.current_thread(),
    )

    # DEV: This local variable will appear in the snapshot and we are using it
    # to test that we can handle unicode strings.
    cake = "After the test there will be ✨ 🍰 ✨ in the annex"

    buffer_size = 30 * (1 << 10)
    encoder = BatchJsonEncoder({Snapshot: SnapshotJsonEncoder(None)}, buffer_size=buffer_size)

    s.line()

    snapshot_size = encoder.put(s)

    n_snapshots = buffer_size // snapshot_size

    with pytest.raises(BufferFull):
        for _ in range(2 * n_snapshots):
            encoder.put(s)

    count = encoder.count
    payload = encoder.encode()
    decoded = json.loads(payload.decode())
    assert len(decoded) == count
    assert n_snapshots <= count
    assert (
        utils.serialize(cake) == decoded[0]["debugger.snapshot"]["captures"]["lines"]["42"]["locals"]["cake"]["value"]
    )
    assert encoder.encode() is None
    assert encoder.encode() is None
    assert encoder.count == 0


def test_batch_flush_reencode():
    s = Snapshot(
        probe=create_snapshot_line_probe(probe_id="batch-test", source_file="foo.py", line=42),
        frame=inspect.currentframe(),
        thread=threading.current_thread(),
    )

    s.line()

    encoder = BatchJsonEncoder({Snapshot: SnapshotJsonEncoder(None)})

    snapshot_total_size = sum(encoder.put(s) for _ in range(2))
    assert encoder.count == 2
    assert len(encoder.encode()) == snapshot_total_size + 3

    a, b = encoder.put(s), encoder.put(s)
    assert abs(a - b) < 1024
    assert encoder.count == 2
    assert len(encoder.encode()) == a + b + 3


# ---- Side effects ----


def test_serialize_side_effects():
    assert utils.serialize(SideEffects()) == "SideEffects()"


@pytest.mark.parametrize(
    "args,expected",
    [
        ({"bar": 42}, "bar=42"),
        ({"bar": [42, 43]}, "bar=list(42, 43)"),
        ({"bar": (42, None)}, "bar=tuple(42, None)"),
        ({"bar": {42, 43}}, "bar=set(42, 43)"),
        ({"bar": {"b": [43, 44]}}, "bar={'b': list()}"),
    ],
)
def test_format_message(args, expected):
    assert format_message("foo", {k: utils.capture_value(v, level=0) for k, v in args.items()}) == "foo(%s)" % expected


def test_encoding_none():
    assert utils.capture_value(None) == {"isNull": True, "type": "NoneType"}


class CountBudget(object):
    """Make stopping condition for the value capturing deterministic."""

    __name__ = "CountBudget"

    def __init__(self, counts):
        self.counts = counts

    def __call__(self, _):
        if self.counts:
            self.counts -= 1
            return False
        return True


@pytest.mark.parametrize(
    "count,result",
    [
        (1, {"entries": [], "notCapturedReason": "CountBudget", "size": 1, "type": "dict"}),
        (
            5,
            {
                "type": "dict",
                "entries": [
                    (
                        {"type": "str", "value": "'a'"},
                        {
                            "type": "dict",
                            "entries": [
                                (
                                    {"type": "str", "notCapturedReason": "CountBudget"},
                                    {"type": "dict", "notCapturedReason": "CountBudget", "size": 1},
                                )
                            ],
                            "size": 1,
                        },
                    )
                ],
                "size": 1,
            },
        ),
        (
            10,
            {
                "type": "dict",
                "entries": [
                    (
                        {"type": "str", "value": "'a'"},
                        {
                            "type": "dict",
                            "entries": [
                                (
                                    {"type": "str", "value": "'a'"},
                                    {
                                        "type": "dict",
                                        "entries": [
                                            (
                                                {"type": "str", "value": "'a'"},
                                                {
                                                    "type": "dict",
                                                    "entries": [],
                                                    "size": 1,
                                                    "notCapturedReason": "CountBudget",
                                                },
                                            )
                                        ],
                                        "size": 1,
                                    },
                                )
                            ],
                            "size": 1,
                        },
                    )
                ],
                "size": 1,
            },
        ),
    ],
)
def test_encoding_stopping_cond_level(count, result):
    # Create a self-recursive object
    a = {}
    a["a"] = a

    assert utils.capture_value(a, level=300, stopping_cond=CountBudget(count)) == result


@pytest.mark.parametrize("count,nfields", [(1, 0), (5, 2), (10, 5)])
def test_encoding_stopping_cond_fields(count, nfields):
    class Obj(object):
        pass

    a = Obj()
    a.__dict__ = {"field%d" % i: i for i in range(100)}

    result = utils.capture_value(a, stopping_cond=CountBudget(count))

    assert result["type"] == "Obj" if PY2 else "test_encoding_stopping_cond_fields.<locals>.Obj"
    assert result["notCapturedReason"] == "CountBudget"
    # We cannot assert fields because the order is not guaranteed
    assert len(result["fields"]) == nfields


@pytest.mark.parametrize(
    "count,result",
    [
        (1, {"notCapturedReason": "CountBudget", "elements": [], "type": "list", "size": 100}),
        (
            5,
            {
                "notCapturedReason": "CountBudget",
                "elements": [{"type": "int", "value": "0"}, {"type": "int", "value": "1"}],
                "type": "list",
                "size": 100,
            },
        ),
        (
            10,
            {
                "notCapturedReason": "CountBudget",
                "elements": [
                    {"type": "int", "value": "0"},
                    {"type": "int", "value": "1"},
                    {"type": "int", "value": "2"},
                    {"type": "int", "value": "3"},
                    {"notCapturedReason": "CountBudget", "type": "int"},
                ],
                "type": "list",
                "size": 100,
            },
        ),
    ],
)
def test_encoding_stopping_cond_collection_size(count, result):
    assert utils.capture_value(list(range(100)), stopping_cond=CountBudget(count)) == result


@pytest.mark.parametrize(
    "count,result",
    [
        (1, {"notCapturedReason": "CountBudget", "size": 100, "type": "dict", "entries": []}),
        (
            5,
            {
                "notCapturedReason": "CountBudget",
                "size": 100,
                "type": "dict",
                "entries": [
                    ({"type": "int", "value": "0"}, {"type": "int", "value": "0"}),
                    (
                        {"notCapturedReason": "CountBudget", "type": "int"},
                        {"notCapturedReason": "CountBudget", "type": "int"},
                    ),
                ],
            },
        ),
        (
            20,
            {
                "notCapturedReason": "CountBudget",
                "size": 100,
                "type": "dict",
                "entries": [
                    ({"type": "int", "value": "0"}, {"type": "int", "value": "0"}),
                    ({"type": "int", "value": "1"}, {"type": "int", "value": "1"}),
                    ({"type": "int", "value": "2"}, {"type": "int", "value": "2"}),
                    ({"type": "int", "value": "3"}, {"type": "int", "value": "3"}),
                    ({"type": "int", "value": "4"}, {"type": "int", "value": "4"}),
                    ({"type": "int", "value": "5"}, {"type": "int", "value": "5"}),
                    (
                        {"notCapturedReason": "CountBudget", "type": "int"},
                        {"notCapturedReason": "CountBudget", "type": "int"},
                    ),
                ],
            },
        ),
    ],
)
def test_encoding_stopping_cond_map_size(count, result):
    assert utils.capture_value({i: i for i in range(100)}, stopping_cond=CountBudget(count)) == result
