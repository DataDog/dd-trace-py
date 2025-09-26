from dis import dis
import re

import pytest

from ddtrace.debugging._expressions import dd_compile
from ddtrace.internal.safety import SafeObjectProxy


class SideEffect(Exception):
    pass


class CustomObject(object):
    def __init__(self, name, level=2):
        self.name = name
        self.myField = "hello"
        self._privateField = "private"
        if level:
            self.collectionField = [CustomObject("foo%d" % _, 0) for _ in range(10)]
            self.field1 = CustomObject("field1", level - 1)
            self.field2 = CustomObject("field2", level - 1)

    def __contains__(self, item):
        raise SideEffect("contains")


class CustomAttr(object):
    def __init__(self):
        self.field = "x"

    def __getattribute__(self, prop):
        return object.__getattribute__(self, prop) + "custom"


class CustomList(list):
    def __getitem__(self, index):
        return str(list.__getitem__(self, index)) + "custom"


class CustomDict(dict):
    def __getitem__(self, name):
        return dict.__getitem__(self, name) + "custom"


@pytest.mark.parametrize(
    "ast, _locals, value",
    [
        # Test references with operations
        ({"len": {"ref": "payload"}}, {"payload": "hello"}, 5),
        ({"len": {"getmember": [{"ref": "self"}, "collectionField"]}}, {"self": CustomObject("expr")}, 10),
        ({"len": {"getmember": [{"ref": "self"}, "_privateField"]}}, {"self": CustomObject("expr")}, len("private")),
        ({"len": {"getmember": [{"ref": "self"}, "bogusField"]}}, {"self": CustomObject("expr")}, AttributeError),
        ({"len": {"ref": "payload"}}, {}, NameError),
        # Test plain references
        ({"ref": "hits"}, {"hits": 42}, 42),
        ({"getmember": [{"ref": "self"}, "name"]}, {"self": CustomObject("test-me")}, "test-me"),
        (
            {"getmember": [{"getmember": [{"ref": "self"}, "field1"]}, "name"]},
            {"self": CustomObject("test-me")},
            "field1",
        ),
        # Test index reference
        ({"index": [{"ref": "arr"}, 1]}, {"arr": ["hello", "world"]}, "world"),
        ({"index": [{"ref": "arr"}, 100]}, {"arr": ["hello", "world"]}, IndexError),
        ({"index": [{"ref": "dict"}, "world"]}, {"dict": {"hello": "hi", "world": "space"}}, "space"),
        ({"index": [{"ref": "dict"}, "bogus_index"]}, {"dict": {"hello": "hi", "world": "space"}}, KeyError),
        # Test getmember and index have no sideeffects
        ({"getmember": [{"ref": "obj"}, "field"]}, {"obj": CustomAttr()}, "x"),
        ({"index": [{"ref": "arr"}, 1]}, {"arr": CustomList(["hello", "world"])}, "world"),
        ({"index": [{"ref": "dict"}, "world"]}, {"dict": CustomDict({"hello": "hi", "world": "space"})}, "space"),
        # Test argument predicates and operations
        ({"contains": [{"ref": "payload"}, "hello"]}, {"payload": "hello world"}, True),
        ({"eq": [{"ref": "hits"}, True]}, {"hits": True}, True),
        ({"eq": [{"ref": "hits"}, None]}, {"hits": None}, True),
        ({"substring": [{"ref": "payload"}, 4, 7]}, {"payload": "hello world"}, "hello world"[4:7]),
        ({"any": [{"ref": "collection"}, {"isEmpty": {"ref": "@it"}}]}, {"collection": ["foo", "bar", ""]}, True),
        ({"any": [{"ref": "coll"}, {"isEmpty": {"ref": "@value"}}]}, {"coll": {0: "foo", 1: "bar", 2: ""}}, True),
        ({"any": [{"ref": "coll"}, {"isEmpty": {"ref": "@value"}}]}, {"coll": {0: "foo", 1: "bar", 2: "baz"}}, False),
        ({"any": [{"ref": "coll"}, {"isEmpty": {"ref": "@key"}}]}, {"coll": {"foo": 0, "bar": 1, "": 2}}, True),
        ({"any": [{"ref": "coll"}, {"isEmpty": {"ref": "@key"}}]}, {"coll": {"foo": 0, "bar": 1, "baz": 2}}, False),
        ({"startsWith": [{"ref": "local_string"}, "hello"]}, {"local_string": "hello world!"}, True),
        ({"startsWith": [{"ref": "local_string"}, "world"]}, {"local_string": "hello world!"}, False),
        (
            {"filter": [{"ref": "collection"}, {"not": {"isEmpty": {"ref": "@it"}}}]},
            {"collection": ["foo", "bar", ""]},
            ["foo", "bar"],
        ),
        (
            {"filter": [{"ref": "collection"}, {"not": {"isEmpty": {"ref": "@it"}}}]},
            {"collection": ("foo", "bar", "")},
            ("foo", "bar"),
        ),
        (
            {"filter": [{"ref": "collection"}, {"not": {"isEmpty": {"ref": "@it"}}}]},
            {"collection": {"foo", "bar", ""}},
            {"foo", "bar"},
        ),
        (
            {"filter": [{"ref": "collection"}, {"not": {"isEmpty": {"ref": "@value"}}}]},
            {"collection": {1: "foo", 2: "bar", 3: ""}},
            {1: "foo", 2: "bar"},
        ),
        (
            {"filter": [{"ref": "collection"}, {"not": {"isEmpty": {"ref": "@key"}}}]},
            {"collection": {"foo": 1, "bar": 2, "": 3}},
            {"foo": 1, "bar": 2},
        ),
        ({"contains": [{"ref": "payload"}, "hello"]}, {"payload": CustomObject("contains")}, SideEffect),
        (
            {"contains": [{"ref": "payload"}, "hello"]},
            {"payload": SafeObjectProxy.safe(CustomObject("contains"))},
            False,
        ),
        ({"contains": [{"ref": "payload"}, "name"]}, {"payload": SafeObjectProxy.safe(CustomObject("contains"))}, True),
        ({"matches": [{"ref": "payload"}, "[0-9]+"]}, {"payload": "42"}, True),
        # Test literal values
        (42, {}, 42),
        (True, {}, True),
        ({"or": [{"ref": "bar"}, {"ref": "foo"}]}, {"bar": 42}, 42),
        ({"and": [{"ref": "bar"}, {"ref": "foo"}]}, {"bar": 0}, 0),
        ({"or": [{"ref": "bar"}, {"ref": "foo"}]}, {"bar": 0}, NameError),
        ({"and": [{"ref": "bar"}, {"ref": "foo"}]}, {"bar": 42}, NameError),
        ({"isDefined": "foobar"}, {"bar": 42}, False),
        ({"isDefined": "bar"}, {"bar": 42}, True),
        ({"instanceof": [{"ref": "bar"}, "int"]}, {"bar": 42}, True),
        ({"instanceof": [{"ref": "bar"}, "BaseException"]}, {"bar": RuntimeError()}, True),
        (
            {"instanceof": [{"ref": "bar"}, f"{CustomObject.__module__}.{CustomObject.__qualname__}"]},
            {"bar": CustomObject("foo")},
            True,
        ),
        # Direct predicates
        ({"not": True}, {}, False),
        ({"not": False}, {}, True),
        ({"isEmpty": {"ref": "empty_str"}}, {"empty_str": ""}, True),
        ({"isEmpty": {"ref": "s"}}, {"s": "foo"}, False),
        ({"isEmpty": {"ref": "empty_list"}}, {"empty_list": []}, True),
        ({"isEmpty": {"ref": "l"}}, {"l": [1]}, False),
        ({"isEmpty": {"ref": "empty_dict"}}, {"empty_dict": {}}, True),
        ({"isEmpty": {"ref": "d"}}, {"d": {"a": 1}}, False),
        # Arg predicates
        ({"ne": [1, 2]}, {}, True),
        ({"ne": [1, 1]}, {}, False),
        ({"gt": [2, 1]}, {}, True),
        ({"gt": [1, 2]}, {}, False),
        ({"ge": [2, 1]}, {}, True),
        ({"ge": [1, 1]}, {}, True),
        ({"ge": [1, 2]}, {}, False),
        ({"lt": [1, 2]}, {}, True),
        ({"lt": [2, 1]}, {}, False),
        ({"le": [1, 2]}, {}, True),
        ({"le": [1, 1]}, {}, True),
        ({"le": [2, 1]}, {}, False),
        ({"all": [{"ref": "collection"}, {"not": {"isEmpty": {"ref": "@it"}}}]}, {"collection": ["foo", "bar"]}, True),
        (
            {"all": [{"ref": "collection"}, {"not": {"isEmpty": {"ref": "@it"}}}]},
            {"collection": ["foo", "bar", ""]},
            False,
        ),
        ({"endsWith": [{"ref": "local_string"}, "world!"]}, {"local_string": "hello world!"}, True),
        ({"endsWith": [{"ref": "local_string"}, "hello"]}, {"local_string": "hello world!"}, False),
        # Nested expressions
        (
            {"len": {"filter": [{"ref": "collection"}, {"gt": [{"ref": "@it"}, 1]}]}},
            {"collection": [1, 2, 3]},
            2,
        ),
        (
            {"getmember": [{"getmember": [{"getmember": [{"ref": "self"}, "field1"]}, "field2"]}, "name"]},
            {"self": CustomObject("test-me")},
            "field2",
        ),
        (
            {
                "any": [
                    {"getmember": [{"ref": "self"}, "collectionField"]},
                    {"startsWith": [{"getmember": [{"ref": "@it"}, "name"]}, "foo"]},
                ]
            },
            {"self": CustomObject("test-me")},
            True,
        ),
        (
            {"and": [{"eq": [{"ref": "hits"}, 42]}, {"gt": [{"len": {"ref": "payload"}}, 5]}]},
            {"hits": 42, "payload": "hello world"},
            True,
        ),
        (
            {"and": [{"eq": [{"ref": "hits"}, 42]}, {"gt": [{"len": {"ref": "payload"}}, 20]}]},
            {"hits": 42, "payload": "hello world"},
            False,
        ),
        (
            {"index": [{"filter": [{"ref": "collection"}, {"gt": [{"ref": "@it"}, 2]}]}, 0]},
            {"collection": [1, 2, 3, 4]},
            3,
        ),
        # Edge cases
        ({"any": [{"ref": "empty_list"}, {"ref": "@it"}]}, {"empty_list": []}, False),
        ({"all": [{"ref": "empty_list"}, {"ref": "@it"}]}, {"empty_list": []}, True),
        ({"count": {"ref": "payload"}}, {"payload": "hello"}, 5),
        ({"substring": [{"ref": "s"}, -5, -1]}, {"s": "hello world"}, "worl"),  # codespell:ignore worl
        ({"substring": [{"ref": "s"}, 0, 100]}, {"s": "hello"}, "hello"),
        ({"matches": [{"ref": "s"}, "["]}, {"s": "a"}, re.error),
        (
            {"or": [True, {"ref": "side_effect"}]},
            {"side_effect": SideEffect("or should short-circuit")},
            True,
        ),
        ({"ref": "@it"}, {}, ValueError),
        (
            {"len": {"filter": [{"ref": "collection"}, {"any": [{"ref": "@it"}, {"eq": [{"ref": "@it"}, 1]}]}]}},
            {"collection": [[1, 2], [3, 4], [5]]},
            1,
        ),
    ],
)
def test_parse_expressions(ast, _locals, value):
    if isinstance(value, type) and issubclass(value, Exception):
        with pytest.raises(value):
            dd_compile(ast)(_locals)
    else:
        compiled = dd_compile(ast)
        assert compiled(_locals) == value, dis(compiled)


def test_side_effects():
    a = CustomList([1, 2])
    assert a[0] == "1custom"
    b = CustomDict({"hello": "world"})
    assert b["hello"] == "worldcustom"
    c = CustomAttr()
    assert c.field == "xcustom"
