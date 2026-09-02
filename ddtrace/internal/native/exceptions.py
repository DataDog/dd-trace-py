def is_panic_exception(exc: BaseException) -> bool:
    """Check whether exc is a pyo3_runtime.PanicException raised by a Rust panic.

    PanicException subclasses BaseException directly rather than Exception, by
    pyo3's design, so that a plain ``except Exception`` can't accidentally
    swallow a Rust panic. It also isn't reliably importable as a class (the
    ``pyo3_runtime`` module only exists once a panic has actually occurred), so
    callers that want to treat it as non-fatal (e.g. because the native object
    involved is being discarded anyway) must identify it by name instead.
    """
    cls = type(exc)
    return cls.__module__ == "pyo3_runtime" and cls.__name__ == "PanicException"
