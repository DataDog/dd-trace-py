"""Unit tests for SymbolResolver and LazyResolver."""

from types import FunctionType

from ddtrace.appsec.sca._resolver import LazyResolver
from ddtrace.appsec.sca._resolver import SymbolResolver


def test_resolve_module_function():
    """Test resolving a module-level function."""
    result = SymbolResolver.resolve("os.path:join")

    assert result is not None
    qualified_name, func = result
    assert qualified_name == "os.path:join"
    assert isinstance(func, FunctionType)
    assert func.__name__ == "join"


def test_resolve_class_method():
    """Test resolving a class method."""
    # Use a built-in class with methods
    result = SymbolResolver.resolve("pathlib:Path.exists")

    assert result is not None
    qualified_name, func = result
    assert qualified_name == "pathlib:Path.exists"
    assert isinstance(func, FunctionType)


def test_resolve_invalid_format_missing_colon():
    """Test that invalid format (missing colon) returns None."""
    result = SymbolResolver.resolve("os.path.join")

    assert result is None


def test_resolve_nonexistent_module():
    """Test that non-existent module returns None."""
    result = SymbolResolver.resolve("nonexistent.module:function")

    assert result is None


def test_resolve_nonexistent_symbol():
    """Test that non-existent symbol returns None."""
    result = SymbolResolver.resolve("os.path:nonexistent_function")

    assert result is None


def test_resolve_non_callable():
    """Test that non-callable symbol returns None."""
    # os.path.sep is a string, not callable
    result = SymbolResolver.resolve("os.path:sep")

    assert result is None


def test_extract_function_from_function():
    """Test extracting function from FunctionType."""

    def test_func():
        pass

    func = SymbolResolver._extract_function(test_func)

    assert func is test_func
    assert isinstance(func, FunctionType)


def test_extract_function_from_method():
    """Test extracting function from bound method."""

    class TestClass:
        def test_method(self):
            pass

    instance = TestClass()
    func = SymbolResolver._extract_function(instance.test_method)

    assert func is TestClass.test_method
    assert isinstance(func, FunctionType)


def test_extract_function_from_staticmethod():
    """Test extracting function from staticmethod."""

    class TestClass:
        @staticmethod
        def test_static():
            pass

    # Get the staticmethod descriptor from the class dict
    static_descriptor = TestClass.__dict__["test_static"]
    func = SymbolResolver._extract_function(static_descriptor)

    assert isinstance(func, FunctionType)
    assert func.__name__ == "test_static"


def test_extract_function_from_classmethod():
    """Test extracting function from classmethod."""

    class TestClass:
        @classmethod
        def test_class(cls):
            pass

    # Get the classmethod descriptor from the class dict
    class_descriptor = TestClass.__dict__["test_class"]
    func = SymbolResolver._extract_function(class_descriptor)

    assert isinstance(func, FunctionType)
    assert func.__name__ == "test_class"


def test_extract_function_from_non_callable():
    """Test that extracting from non-callable returns None."""
    result = SymbolResolver._extract_function("not a function")

    assert result is None


def test_lazy_resolver_add_pending():
    """Test adding pending targets."""
    resolver = LazyResolver()

    resolver.add_pending("module.path:function")
    resolver.add_pending("another.module:function")

    pending = resolver.get_pending()
    assert "module.path:function" in pending
    assert "another.module:function" in pending
    assert len(pending) == 2


def test_lazy_resolver_remove_pending():
    """Test removing pending target."""
    resolver = LazyResolver()

    resolver.add_pending("module.path:function")
    assert "module.path:function" in resolver.get_pending()

    resolver.remove_pending("module.path:function")
    assert "module.path:function" not in resolver.get_pending()


def test_lazy_resolver_retry_pending_success():
    """Test retrying pending targets that now resolve."""
    resolver = LazyResolver()

    # Add a valid target that should resolve
    resolver.add_pending("os.path:join")

    resolved = resolver.retry_pending()

    # Should have resolved successfully
    assert len(resolved) == 1
    qualified_name, func = resolved[0]
    assert qualified_name == "os.path:join"
    assert isinstance(func, FunctionType)

    # Should be removed from pending
    assert "os.path:join" not in resolver.get_pending()


def test_lazy_resolver_retry_pending_still_fails():
    """Test retrying pending targets that still fail."""
    resolver = LazyResolver()

    # Add an invalid target
    resolver.add_pending("nonexistent.module:function")

    resolved = resolver.retry_pending()

    # Should not have resolved
    assert len(resolved) == 0

    # Should still be pending
    assert "nonexistent.module:function" in resolver.get_pending()


def test_lazy_resolver_retry_pending_mixed():
    """Test retrying with mix of successful and failed resolutions."""
    resolver = LazyResolver()

    # Add mix of valid and invalid targets
    resolver.add_pending("os.path:join")  # Valid
    resolver.add_pending("nonexistent.module:function")  # Invalid
    resolver.add_pending("pathlib:Path.exists")  # Valid

    resolved = resolver.retry_pending()

    # Should have resolved the valid ones
    assert len(resolved) == 2
    resolved_names = {name for name, _ in resolved}
    assert "os.path:join" in resolved_names
    assert "pathlib:Path.exists" in resolved_names

    # Only invalid should remain pending
    pending = resolver.get_pending()
    assert len(pending) == 1
    assert "nonexistent.module:function" in pending


def test_lazy_resolver_clear():
    """Test clearing all pending targets."""
    resolver = LazyResolver()

    resolver.add_pending("target1")
    resolver.add_pending("target2")
    assert len(resolver.get_pending()) == 2

    resolver.clear()

    assert len(resolver.get_pending()) == 0


def test_lazy_resolver_add_duplicate():
    """Test that adding duplicate pending target doesn't create duplicates."""
    resolver = LazyResolver()

    resolver.add_pending("module.path:function")
    resolver.add_pending("module.path:function")
    resolver.add_pending("module.path:function")

    pending = resolver.get_pending()
    assert len(pending) == 1
