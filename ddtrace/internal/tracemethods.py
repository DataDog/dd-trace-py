import inspect
from typing import List
from typing import Tuple

import wrapt

from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


def _parse_trace_methods(raw_dd_trace_methods: str) -> List[Tuple[str, str]]:
    """Return a list of the module,methodname tuples to trace based on the
    specification of DD_TRACE_METHODS.

    DD_TRACE_METHODS is specified to be FullyQualifiedModuleName:comma-separated-methods;...

    Note that support for wildcard methods with * is not implemented.
    """
    if not raw_dd_trace_methods:
        return []
    dd_trace_methods = []
    for qualified_methods in raw_dd_trace_methods.split(";"):
        # Validate that methods are specified
        if ":" not in qualified_methods:
            log.warning(
                (
                    "Invalid DD_TRACE_METHODS: %s. "
                    "Methods must be specified after a colon following the fully qualified module."
                ),
                qualified_methods,
            )
            return []

        # Store the prefix and the methods  (eg. for "foo.bar.baz:qux,quux",
        # this is "foo.bar.baz" for the prefix and "qux,quux" for the methods)
        qualified_method_prefix, methods = qualified_methods.split(":")

        if qualified_method_prefix == "__main__":
            # __main__ cannot be used since the __main__ that exists now is not the same as the __main__ that the user
            # application will have. __main__ when sitecustomize module is run is the builtin __main__.
            log.warning(
                "Invalid DD_TRACE_METHODS: %s. Methods cannot be traced on the __main__ module. __main__ when "
                "sitecustomize module is run is the builtin __main__.",
                qualified_methods,
            )
            return []

        # Add the methods to the list of methods to trace
        for method in methods.split(","):
            if not str.isidentifier(method.split(".")[-1]):
                log.warning(
                    "Invalid method name: %r. %s",
                    method,
                    (
                        "You might have a trailing comma."
                        if method == ""
                        else "Method names must be valid Python identifiers."
                    ),
                )
                return []
            dd_trace_methods.append((qualified_method_prefix, method))
    return dd_trace_methods


def _install_trace_methods(raw_dd_trace_methods: str) -> None:
    """Install tracing on the given methods."""
    for module_name, method_name in _parse_trace_methods(raw_dd_trace_methods):
        trace_method(module_name, method_name)


def trace_method(module, method_name):
    # type: (str, str) -> None

    @wrapt.importer.when_imported(module)
    def _(m):
        wrapt.wrap_function_wrapper(m, method_name, trace_wrapper)


def trace_wrapper(wrapped, instance, args, kwargs):
    from ddtrace.trace import tracer

    resource = wrapped.__name__
    if hasattr(instance, "__class__") and instance.__class__ is not type(None):  # noqa: E721
        resource = "%s.%s" % (instance.__class__.__name__, resource)

    # Check for async
    if inspect.iscoroutinefunction(wrapped):

        async def async_wrapper(*a, **kw):
            with tracer.trace("trace.annotation", resource=resource) as span:
                span.set_tag_str("component", "trace")
                return await wrapped(*a, **kw)

        return async_wrapper(*args, **kwargs)

    with tracer.trace("trace.annotation", resource=resource) as span:
        span.set_tag_str("component", "trace")
        return wrapped(*args, **kwargs)
