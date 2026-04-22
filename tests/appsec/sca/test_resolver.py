"""Unit tests for SymbolResolver."""

from types import FunctionType

from ddtrace.appsec.sca._resolver import SymbolResolver


def test_resolve_module_function():
    result = SymbolResolver.resolve("os.path:join")
    assert result is not None
    qualified_name, func = result
    assert qualified_name == "os.path:join"
    assert isinstance(func, FunctionType)
    assert func.__name__ == "join"


def test_resolve_class_method():
    result = SymbolResolver.resolve("pathlib:Path.exists")
    assert result is not None
    qualified_name, func = result
    assert qualified_name == "pathlib:Path.exists"
    assert isinstance(func, FunctionType)


def test_resolve_invalid_format_missing_colon():
    assert SymbolResolver.resolve("os.path.join") is None


def test_resolve_nonexistent_module():
    assert SymbolResolver.resolve("nonexistent.module:function") is None


def test_resolve_nonexistent_symbol():
    assert SymbolResolver.resolve("os.path:nonexistent_function") is None


def test_resolve_non_callable():
    assert SymbolResolver.resolve("os.path:sep") is None


def test_extract_function_from_function():
    def test_func():
        pass

    func = SymbolResolver._extract_function(test_func)
    assert func is test_func
    assert isinstance(func, FunctionType)


def test_extract_function_from_method():
    class TestClass:
        def test_method(self):
            pass

    instance = TestClass()
    func = SymbolResolver._extract_function(instance.test_method)
    assert func is TestClass.test_method
    assert isinstance(func, FunctionType)


def test_extract_function_from_non_callable():
    assert SymbolResolver._extract_function("not a function") is None


def test_extract_function_unwraps_wrapped():
    """__wrapped__ attribute is unwrapped (for wrapt FunctionWrapper compatibility)."""

    def original():
        pass

    class FakeWrapper:
        def __init__(self):
            self.__wrapped__ = original

        def __call__(self):
            pass

    wrapper = FakeWrapper()
    func = SymbolResolver._extract_function(wrapper)
    assert func is original


def test_resolve_class_method_gets_dict_function():
    """Resolving a class method returns the actual function from __dict__, not a wrapper."""
    import http.client

    result = SymbolResolver.resolve("http.client:HTTPConnection.request")
    assert result is not None
    _, func = result
    # Should be the actual function from __dict__, not a new wrapper
    dict_func = SymbolResolver._extract_function(http.client.HTTPConnection.__dict__["request"])
    assert func is dict_func
