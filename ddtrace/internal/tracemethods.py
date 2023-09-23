import typing

import wrapt


def _parse_trace_methods(raw_dd_trace_methods):
    # type: (str) -> typing.List[str]
    """Return the methods to trace based on the specification of DD_TRACE_METHODS.

    DD_TRACE_METHODS is specified to be FullyQualifiedClassOrModuleName[comma-separated-methods]

    Note that support for wildcard methods ([*]) is not implemented.
    """
    if not raw_dd_trace_methods:
        return []

    dd_trace_methods = []
    for qualified_methods in raw_dd_trace_methods.split(";"):
        # Validate that methods are specified
        if "[" not in qualified_methods or "]" not in qualified_methods:
            raise ValueError(
                (
                    "Invalid DD_TRACE_METHODS: %s. "
                    "Methods must be specified in square brackets following the fully qualified module or class name."
                )
                % qualified_methods
            )

        # Store the prefix of the qualified method name (eg. for "foo.bar.baz[qux,quux]", this is "foo.bar.baz")
        qualified_method_prefix = qualified_methods.split("[")[0]

        if qualified_method_prefix == "__main__":
            # __main__ cannot be used since the __main__ that exists now is not the same as the __main__ that the user
            # application will have. __main__ when sitecustomize module is run is the builtin __main__.
            raise ValueError(
                "Invalid DD_TRACE_METHODS: %s. Methods cannot be traced on the __main__ module." % qualified_methods
            )

        # Get the class or module name of the method (eg. for "foo.bar.baz[qux,quux]", this is "baz[qux,quux]")
        class_or_module_with_methods = qualified_methods.split(".")[-1]

        # Strip off the leading 'moduleOrClass[' and trailing ']'
        methods = class_or_module_with_methods.split("[")[1]
        methods = methods[:-1]

        # Add the methods to the list of methods to trace
        for method in methods.split(","):
            if not str.isidentifier(method):
                raise ValueError(
                    "Invalid method name: %r. %s"
                    % (
                        method,
                        "You might have a trailing comma."
                        if method == ""
                        else "Method names must be valid Python identifiers.",
                    )
                )
            dd_trace_methods.append("%s.%s" % (qualified_method_prefix, method))

    return dd_trace_methods


def _install_trace_methods(raw_dd_trace_methods):
    # type: (str) -> None
    """Install tracing on the given methods."""
    for qualified_method in _parse_trace_methods(raw_dd_trace_methods):
        # We don't know if the method is a class method or a module method, so we need to assume it's a module
        # and if the import fails then go a level up and try again.
        base_module_guess = ".".join(qualified_method.split(".")[:-1])
        method_name = qualified_method.split(".")[-1]
        module = None

        while base_module_guess:
            try:
                module = __import__(base_module_guess)
            except ImportError:
                # Add the class to the method name
                method_name = "%s.%s" % (base_module_guess.split(".")[-1], method_name)
                base_module_guess = ".".join(base_module_guess.split(".")[:-1])
            else:
                break

        if module is None:
            raise ImportError("Could not import module for %r" % qualified_method)

        trace_method(base_module_guess, method_name)


def trace_method(module, method_name):
    # type: (str, str) -> None

    @wrapt.importer.when_imported(module)
    def _(m):
        wrapt.wrap_function_wrapper(m, method_name, trace_wrapper)


def trace_wrapper(wrapped, instance, args, kwargs):
    from ddtrace import tracer

    resource = wrapped.__name__
    if hasattr(instance, "__class__") and instance.__class__ is not type(None):  # noqa: E721
        resource = "%s.%s" % (instance.__class__.__name__, resource)

    with tracer.trace("trace.annotation", resource=resource) as span:
        span.set_tag_str("component", "trace")
        return wrapped(*args, **kwargs)
