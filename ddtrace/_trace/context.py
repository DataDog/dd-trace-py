from ddtrace.internal.native._native import ContextData


class Context(ContextData):
    """Represents the state required to propagate a trace across execution
    boundaries.
    """

    __slots__ = ["__weakref__"]
