import logging


_PACKAGE = "ddtrace_testing"
_LOGGER_NAMESPACE = "ddtrace.testing"


def get_logger(name: str) -> logging.Logger:
    """Return a logger in the historical ``ddtrace.testing`` namespace.

    Logger names are user-facing (``logging.getLogger("ddtrace")`` configuration, the
    ``[Datadog Test Optimization]`` handler on ``ddtrace.testing``), so moving the package to
    ``ddtrace_testing`` must not change them.
    """
    if name == _PACKAGE or name.startswith(_PACKAGE + "."):
        name = _LOGGER_NAMESPACE + name[len(_PACKAGE) :]
    return logging.getLogger(name)
