"""Python helpers for errors raised by the native extension.

Plain Python, so it lives outside ``ddtrace/internal/native/``, which holds only compiled code and moves to the
ddtrace-native package as is.
"""


def is_panic_exception(exc: BaseException) -> bool:
    """Check whether exc is a pyo3_runtime.PanicException raised by a Rust panic."""
    cls = type(exc)
    return cls.__module__ == "pyo3_runtime" and cls.__name__ == "PanicException"
