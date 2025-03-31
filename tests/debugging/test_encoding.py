# -*- coding: utf-8 -*-

from collections import defaultdict
import inspect
import json
import sys
import threading

import pytest

from ddtrace.debugging._encoding import JSONTree
from ddtrace.debugging._encoding import LogSignalJsonEncoder
from ddtrace.debugging._encoding import SignalQueue
from ddtrace.debugging._probe.model import MAXSIZE
from ddtrace.debugging._probe.model import CaptureLimits
from ddtrace.debugging._signal import utils
from ddtrace.debugging._signal.snapshot import Snapshot
from ddtrace.debugging._signal.snapshot import _capture_context
from ddtrace.debugging._signal.utils import BUILTIN_MAPPING_TYPES
from ddtrace.debugging._signal.utils import BUILTIN_SEQUENCE_TYPES
from ddtrace.internal._encoding import BufferFull
from tests.debugging.test_config import debugger_config
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
        (b"Hello", "b'Hello'"),
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
            "({'Hello': [None, 42, True, None, {b'World'}, 0.07]})",
        ),
    ],
)
def test_serialize(value, serialized):
    assert utils.serialize(value, level=-1) == serialized


def test_serialize_custom_object():
    assert utils.serialize(Custom(), level=-1) == (
        "Custom(some_arg=({'Hello': [None, 42, True, None, {b'World'}, 0.07]}))"
    )

    assert utils.serialize(Custom(), 1) == "Custom(some_arg=<class 'tuple'>)"
    assert utils.serialize(Custom(), 2) == "Custom(some_arg=(<class 'dict'>))"
    assert utils.serialize(Custom(), 3) == "Custom(some_arg=({'Hello': <class 'list'>}))"
    assert utils.serialize(Custom(), 4) == "Custom(some_arg=({'Hello': [None, 42, True, None, <class 'set'>, 0.07]}))"

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
    assert [_["function"] for _ in serialized["stacktrace"][-3:]] == ["c", "b", "a"]
    assert serialized["message"] == "'bad'"


def capture_context(*args, **kwargs):
    return _capture_context(sys._getframe(1), *args, **kwargs)


def test_capture_context_default_level():
    def _(self=tree):
        return capture_context((None, None, None), limits=CaptureLimits(max_level=0))

    self = _()["arguments"]["self"]
    assert self["fields"]["root"]["notCapturedReason"] == "depth"


def test_capture_context_one_level():
    def _(self=tree):
        return capture_context((None, None, None), limits=CaptureLimits(max_level=1))

    self = _()["arguments"]["self"]

    assert self["fields"]["root"]["fields"]["left"] == {"notCapturedReason": "depth", "type": "Node"}

    assert self["fields"]["root"]["fields"]["name"]["value"] == "'0'"
    assert "fields" not in self["fields"]["root"]["fields"]["name"]


def test_capture_context_two_level():
    def _(self=tree):
        return capture_context((None, None, None), limits=CaptureLimits(max_level=2))

    self = _()["arguments"]["self"]
    assert self["fields"]["root"]["fields"]["left"]["fields"]["right"] == {"notCapturedReason": "depth", "type": "Node"}


def test_capture_context_three_level():
    def _(self=tree):
        return capture_context((None, None, None), limits=CaptureLimits(max_level=3))

    context = _()
    self = context["arguments"]["self"]
    assert self["fields"]["root"]["fields"]["left"]["fields"]["right"]["fields"]["right"]["isNull"], context
    assert self["fields"]["root"]["fields"]["left"]["fields"]["right"]["fields"]["left"]["isNull"], context
    assert "fields" not in self["fields"]["root"]["fields"]["left"]["fields"]["right"]["fields"]["right"], context


def test_capture_context_exc():
    try:
        raise Exception("test", "me")
    except Exception:

        def _():
            return capture_context(sys.exc_info())

        context = _()

        exc = context.pop("throwable")
        assert context["arguments"] == {}
        assert context["locals"] == {
            "@exception": {
                "type": "Exception",
                "fields": {
                    "args": {
                        "type": "tuple",
                        "elements": [{"type": "str", "value": "'test'"}, {"type": "str", "value": "'me'"}],
                        "size": 2,
                    },
                    "__cause__": {"type": "NoneType", "isNull": True},
                    "__context__": {"type": "NoneType", "isNull": True},
                    "__suppress_context__": {"type": "bool", "value": "False"},
                },
            }
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

    buffer_size = 30 * (1 << 20)
    queue = SignalQueue(encoder=LogSignalJsonEncoder(None), buffer_size=buffer_size)

    s.line({})

    snapshot_size = queue.put(s)

    n_snapshots = buffer_size // snapshot_size

    with pytest.raises(BufferFull):
        for _ in range(2 * n_snapshots):
            queue.put(s)

    count = queue.count
    payload = queue.flush()
    decoded = json.loads(payload.decode())
    assert len(decoded) == count
    assert n_snapshots <= count + 1  # Allow for rounding errors
    assert (
        utils.serialize(cake)
        == decoded[0]["debugger"]["snapshot"]["captures"]["lines"]["42"]["locals"]["cake"]["value"]
    )
    assert queue.flush() is None
    assert queue.flush() is None
    assert queue.count == 0


def test_batch_flush_reencode():
    s = Snapshot(
        probe=create_snapshot_line_probe(probe_id="batch-test", source_file="foo.py", line=42),
        frame=inspect.currentframe(),
        thread=threading.current_thread(),
    )

    s.line({})

    queue = SignalQueue(LogSignalJsonEncoder(None))

    snapshot_total_size = sum(queue.put(s) for _ in range(2))
    assert queue.count == 2
    assert len(queue.flush()) == snapshot_total_size + 3

    a, b = queue.put(s), queue.put(s)
    assert abs(a - b) < 1024
    assert queue.count == 2
    assert len(queue.flush()) == a + b + 3


# ---- Side effects ----


def test_serialize_side_effects():
    assert utils.serialize(SideEffects()) == "SideEffects()"


def test_encoding_none():
    assert utils.capture_value(None) == {"isNull": True, "type": "NoneType"}


def test_encoding_zero_fields():
    assert utils.capture_value(Custom(), maxfields=0) == {
        "fields": {},
        "notCapturedReason": "fieldCount",
        "type": "Custom",
    }


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

    assert result["type"] == "test_encoding_stopping_cond_fields.<locals>.Obj"
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


def test_json_tree():
    tree = JSONTree(r'{"a": 1, "b": {"a": [{"a": 1, "b": 2}, {"a": 1, "notCapturedReason": "depth"}], "b": 2}}')

    def node_to_tuple(node: JSONTree.Node):
        return (
            node.start,
            node.end,
            node.level,
            node.not_captured,
            node.pruned,
            [node_to_tuple(_) for _ in node.children],
        )

    assert node_to_tuple(tree.root) == (
        0,
        88,
        0,
        False,
        0,
        [
            (
                14,
                87,
                1,
                False,
                0,
                [
                    (21, 37, 2, False, 0, []),
                    (39, 77, 2, True, 0, []),
                ],
            )
        ],
    )

    assert [node_to_tuple(_) for _ in sorted(tree.leaves)] == [
        (39, 77, 2, True, 0, []),
        (21, 37, 2, False, 0, []),
    ]


@pytest.mark.parametrize(
    "size, expected",
    [
        (
            800,
            r"""{
                "keep": {"type": "list", "size":2, "elements": [
                    {"type": "str", "value": "aaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
                    {"type": "str", "value": "aaaaaaaaaaaaaaaaaaaaaaaaaaaa"}
                ]},
                "prune": {"type": "list", "size":2, "elements": [
                    {"type": "Custom", "notCapturedReason": "depth"},
                    {"type": "Custom", "notCapturedReason": "depth"}
                ]}
            }""",
        ),
        (
            440,
            r"""{
                "keep": {"type": "list", "size":2, "elements": [
                    {"type": "str", "value": "aaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
                    {"type": "str", "value": "aaaaaaaaaaaaaaaaaaaaaaaaaaaa"}
                ]},
                "prune": {"type": "list", "size":2, "elements": [
                    {"pruned":true},
                    {"pruned":true}
                ]}
            }""",
        ),
        (
            350,
            r"""{
                "keep": {"type": "list", "size":2, "elements": [
                    {"type": "str", "value": "aaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
                    {"type": "str", "value": "aaaaaaaaaaaaaaaaaaaaaaaaaaaa"}
                ]},
                "prune": {"pruned":true}
            }""",
        ),
        (
            270,
            r"""{
                "keep": {"type": "list", "size":2, "elements": [
                    {"type": "str", "value": "aaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
                    {"pruned":true}
                ]},
                "prune": {"pruned":true}
            }""",
        ),
        (
            240,
            r"""{
                "keep": {"type": "list", "size":2, "elements": [
                    {"pruned":true},
                    {"pruned":true}
                ]},
                "prune": {"pruned":true}
            }""",
        ),
        (
            120,
            r"""{
                "keep": {"pruned":true},
                "prune": {"pruned":true}
            }""",
        ),
        (20, r'{"pruned":true}'),
    ],
)
def test_json_pruning_not_capture_depth(size, expected):
    class TestEncoder(LogSignalJsonEncoder):
        MAX_SIGNAL_SIZE = size
        MIN_LEVEL = 0

    assert (
        TestEncoder(None).pruned(
            r"""{
                "keep": {"type": "list", "size":2, "elements": [
                    {"type": "str", "value": "aaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
                    {"type": "str", "value": "aaaaaaaaaaaaaaaaaaaaaaaaaaaa"}
                ]},
                "prune": {"type": "list", "size":2, "elements": [
                    {"type": "Custom", "notCapturedReason": "depth"},
                    {"type": "Custom", "notCapturedReason": "depth"}
                ]}
            }""",
        )
        == expected
    )


def test_capture_value_redacted_type():
    class Foo:
        def __init__(self):
            self.secret_foo = 42

    class Bar:
        def __init__(self):
            self.secret_bar = "deadbeef"

    class Baz:
        def __init__(self):
            self.secret_baz = "hello"

    class SecretHolder:
        def __init__(self):
            self.token = "secret"

    with debugger_config(
        DD_DYNAMIC_INSTRUMENTATION_REDACTED_TYPES=",".join(
            (
                utils.qualname(Foo),
                utils.qualname(Bar),
                "*.Secret*",
            )
        )
    ):
        assert utils.capture_value([Foo(), Bar(), Baz(), SecretHolder()]) == {
            "type": "list",
            "elements": [
                utils.redacted_type(Foo),
                utils.redacted_type(Bar),
                {
                    "type": "test_capture_value_redacted_type.<locals>.Baz",
                    "fields": {
                        "secret_baz": {
                            "type": "str",
                            "value": "'hello'",
                        }
                    },
                },
                utils.redacted_type(SecretHolder),
            ],
            "size": 4,
        }


@pytest.mark.parametrize("_type", BUILTIN_MAPPING_TYPES)
def test_capture_value_mapping_type(_type):
    try:
        d = _type({"bar": 42})
    except TypeError:
        # defaultdict requires a factory
        d = _type(int, {"bar": 42})

    assert utils.capture_value(d) == {
        "type": utils.qualname(_type),
        "entries": [
            (
                {"type": "str", "value": "'bar'"},
                {"type": "int", "value": "42"},
            ),
        ],
        "size": 1,
    }


@pytest.mark.parametrize("_type", BUILTIN_SEQUENCE_TYPES)
def test_capture_value_sequence_type(_type):
    s = _type(["foo"])

    assert utils.capture_value(s) == {
        "type": utils.qualname(_type),
        "elements": [
            {"type": "str", "value": "'foo'"},
        ],
        "size": 1,
    }


@pytest.mark.parametrize(
    "value,expected",
    [
        (defaultdict(int, {"bar": 42}), "{'bar': 42}"),
        (frozenset({"foo"}), "{'foo'}"),
    ],
)
def test_serialize_builtins(value, expected):
    assert utils.serialize(value) == expected
