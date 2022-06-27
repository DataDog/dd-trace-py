import pytest

from ddtrace.debugging._expressions import dd_compile
from ddtrace.internal.safety import SafeObjectProxy


class SideEffect(Exception):
    pass


class CustomObject(object):
    def __init__(self, name, level=2):
        self.name = name
        self.myField = "hello"
        if level:
            self.collectionField = [CustomObject("foo%d" % _, 0) for _ in range(10)]
            self.field1 = CustomObject("field1", level - 1)
            self.field2 = CustomObject("field2", level - 1)

    def __contains__(self, item):
        raise SideEffect("contains")


@pytest.mark.parametrize(
    "ast, _locals, value",
    [
        # Test references with operations
        ({"len": "#payload"}, {"payload": "hello"}, 5),
        ({"len": ".collectionField"}, {"self": CustomObject("expr")}, 10),
        ({"len": ".bogusField"}, {"self": CustomObject("expr")}, AttributeError),
        ({"len": "^payload"}, {"payload": "hello"}, 5),
        ({"len": "^payload"}, {}, KeyError),
        # Test plain references
        (".name", {"self": CustomObject("test-me")}, "test-me"),
        ("#hits", {"hits": 42}, 42),
        ("^hits", {"hits": 42}, 42),
        # Test argument predicates and operations
        ({"contains": ["#payload", "hello"]}, {"payload": "hello world"}, True),
        ({"eq": ["#hits", True]}, {"hits": True}, True),
        ({"substring": ["#payload", 4, 7]}, {"payload": "hello world"}, "hello world"[4:7]),
        ({"any": ["#collection", {"isEmpty": "@it"}]}, {"collection": ["foo", "bar", ""]}, True),
        ({"startsWith": ["#local_string", "hello"]}, {"local_string": "hello world!"}, True),
        ({"startsWith": ["#local_string", "world"]}, {"local_string": "hello world!"}, False),
        ({"filter": ["#collection", {"not": {"isEmpty": "@it"}}]}, {"collection": ["foo", "bar", ""]}, ["foo", "bar"]),
        ({"filter": ["#collection", {"not": {"isEmpty": "@it"}}]}, {"collection": ("foo", "bar", "")}, ("foo", "bar")),
        ({"filter": ["#collection", {"not": {"isEmpty": "@it"}}]}, {"collection": {"foo", "bar", ""}}, {"foo", "bar"}),
        ({"contains": ["#payload", "hello"]}, {"payload": CustomObject("contains")}, SideEffect),
        ({"contains": ["#payload", "hello"]}, {"payload": SafeObjectProxy.safe(CustomObject("contains"))}, False),
        ({"contains": ["#payload", "name"]}, {"payload": SafeObjectProxy.safe(CustomObject("contains"))}, True),
        ({"matches": ["#payload", "[0-9]+"]}, {"payload": "42"}, True),
        # Test literal values
        (42, {}, 42),
        (True, {}, True),
    ],
)
def test_parse_expressions(ast, _locals, value):
    compiled = dd_compile(ast)

    if isinstance(value, type) and issubclass(value, Exception):
        with pytest.raises(value):
            compiled(_locals)
    else:
        assert compiled(_locals) == value
