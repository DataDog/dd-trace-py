from dis import dis
import re

import pytest

from ddtrace.debugging._expressions import DDCompiler
from ddtrace.debugging._expressions import DDExpression
from ddtrace.debugging._expressions import _is_one_shot_iterator
from ddtrace.debugging._expressions import dd_compile
from ddtrace.debugging._expressions import instanceof
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
        # contains on non-builtin types raises TypeError before any __contains__ is invoked
        ({"contains": [{"ref": "payload"}, "hello"]}, {"payload": CustomObject("contains")}, TypeError),
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
        ({"isDefined": {"ref": "foobar"}}, {"bar": 42}, False),
        ({"isDefined": {"ref": "bar"}}, {"bar": 42}, True),
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


# ---- instanceof coverage ----


def test_instanceof_non_matching_qualname():
    """instanceof returns False when no MRO entry matches the qualified name (line 87)."""
    assert instanceof(42, "some.Unknown.Type") is False


def test_instanceof_attribute_error_in_mro():
    """instanceof catches AttributeError during MRO attribute access (lines 84-85)."""

    class BrokenMeta(type):
        __module__ = property(lambda _: (_ for _ in ()).throw(AttributeError("no module")))  # type: ignore[assignment]

    class BrokenClass(metaclass=BrokenMeta):
        pass

    # Should return False rather than propagating the AttributeError
    assert instanceof(BrokenClass(), "some.Module.BrokenClass") is False


# ---- Compiler invalid-argument errors ----


@pytest.mark.parametrize(
    "ast",
    [
        {"not": {"unknown_op": []}},
        {"or": [{"unknown_op": []}, 1]},
        {"or": [1, {"unknown_op": []}]},
        {"and": [{"unknown_op": []}, 1]},
        {"and": [1, {"unknown_op": []}]},
        {"eq": [{"unknown_op": []}, 1]},
        {"eq": [1, {"unknown_op": []}]},
        {"contains": [{"unknown_op": []}, "x"]},
        {"contains": ["x", {"unknown_op": []}]},
        {"any": [{"unknown_op": []}, True]},
        {"startsWith": [{"unknown_op": []}, "x"]},
        {"startsWith": ["x", {"unknown_op": []}]},
        {"endsWith": [{"unknown_op": []}, "x"]},
        {"endsWith": ["x", {"unknown_op": []}]},
        {"matches": [{"unknown_op": []}, "[0-9]+"]},
        {"matches": ["x", {"unknown_op": []}]},
        {"len": {"unknown_op": []}},
        {"ref": 42},
    ],
)
def test_compile_invalid_argument_raises(ast):
    """Sub-arguments that cannot be compiled raise ValueError at compile time."""
    with pytest.raises(ValueError):
        dd_compile(ast)


# ---- DDExpression.compile failure path ----


def test_dd_expression_compile_failure_falls_back_to_invalid_expression():
    """DDExpression.compile uses _invalid_expression on compiler error (lines 436, 414)."""
    expr = DDExpression.compile({"json": {"unknown_op": []}, "dsl": "bad_expr"})
    # _invalid_expression returns None silently rather than raising
    assert expr.eval({}) is None


# ---- _make_lambda invalid predicate ----


def test_make_lambda_invalid_predicate_raises():
    """_make_lambda raises ValueError when the AST produces no compilable predicate (line 136)."""
    compiler = DDCompiler()
    with pytest.raises(ValueError, match="Invalid predicate"):
        compiler._make_lambda({"unknown_op": []})


# ---- One-shot iterator guard ----


def test_any_refuses_generator():
    """any/all raise TypeError for a generator to avoid exhausting application state."""
    gen = (x for x in [1, 2, 3])
    with pytest.raises(TypeError, match="one-shot iterator"):
        dd_compile({"any": [{"ref": "g"}, True]})({"g": gen})
    # Generator must be untouched — the guard fires before any iteration.
    assert list(gen) == [1, 2, 3]


def test_any_refuses_map_iterator():
    """The one-shot guard uses MRO inspection, so it catches map/zip/filter too."""
    m = map(str, [1, 2, 3])
    with pytest.raises(TypeError, match="one-shot iterator"):
        dd_compile({"any": [{"ref": "m"}, True]})({"m": m})
    assert list(m) == ["1", "2", "3"]


def test_is_one_shot_iterator_catches_async_generator():
    """_is_one_shot_iterator returns True for async generators (__anext__)."""

    async def agen():
        yield 1

    assert _is_one_shot_iterator(agen())


def test_is_one_shot_iterator_not_fooled_by_metaclass_getattribute():
    """_is_one_shot_iterator uses object.__getattribute__ so a metaclass override cannot hide __next__."""

    class LyingMeta(type):
        def __getattribute__(cls, name):
            if name == "__mro__":
                # Lie and return only object's MRO to try to hide __next__
                return (object,)
            return super().__getattribute__(name)

    class FakeNonIterator(metaclass=LyingMeta):
        def __next__(self):
            return 42

    assert _is_one_shot_iterator(FakeNonIterator())


def test_is_one_shot_iterator_false_for_regular_iterables():
    """_is_one_shot_iterator returns False for multi-shot iterables."""
    assert not _is_one_shot_iterator([1, 2, 3])
    assert not _is_one_shot_iterator((1, 2, 3))
    assert not _is_one_shot_iterator({1, 2, 3})
    assert not _is_one_shot_iterator({"a": 1})
    assert not _is_one_shot_iterator("hello")
    assert not _is_one_shot_iterator(range(10))


def test_filter_refuses_generator():
    """filter raises TypeError for a generator to avoid exhausting application state."""
    gen = (x for x in [1, 2, 3])
    with pytest.raises(TypeError, match="one-shot iterator"):
        dd_compile({"filter": [{"ref": "g"}, True]})({"g": gen})
    assert list(gen) == [1, 2, 3]


def test_filter_on_custom_dict_subclass_does_not_call_items_override():
    """filter uses dict.items() directly, bypassing any subclass override."""
    items_called = []

    class TrackedDict(dict):
        def items(self):
            items_called.append(True)
            return super().items()

    d = TrackedDict({"a": 1, "b": 2})
    result = dd_compile({"filter": [{"ref": "d"}, True]})({"d": d})
    assert not items_called, "items() override must not be called"
    assert result == {"a": 1, "b": 2}


def test_any_on_custom_dict_subclass_does_not_call_items_override():
    """any uses dict.items() directly, bypassing any subclass override."""
    items_called = []

    class TrackedDict(dict):
        def items(self):
            items_called.append(True)
            return super().items()

    d = TrackedDict({"a": 1})
    dd_compile({"any": [{"ref": "d"}, True]})({"d": d})
    assert not items_called, "items() override must not be called"


def test_filter_on_custom_sequence_returns_list_not_subclass():
    """filter falls back to list for custom sequence types to avoid __init__ side effects."""
    init_called = []

    class TrackedList(list):
        def __init__(self, *args, **kwargs):
            init_called.append(True)
            super().__init__(*args, **kwargs)

    coll = TrackedList([1, 2, 3])
    init_called.clear()  # reset — we only care about calls triggered by filter

    result = dd_compile({"filter": [{"ref": "l"}, True]})({"l": coll})
    assert type(result) is list
    assert result == [1, 2, 3]
    assert not init_called, "__init__ of subclass must not be called during filter"


@pytest.mark.parametrize("coll_type", [list, tuple, set, frozenset])
def test_filter_preserves_builtin_collection_type(coll_type):
    """filter preserves the type for known-safe builtin collection types."""
    coll = coll_type([1, 2, 3])
    result = dd_compile({"filter": [{"ref": "c"}, True]})({"c": coll})
    assert type(result) is coll_type
