"""Wrapping compatibility suite for dd-trace-py function wrapping.

Hand-written tests that exercise the wrapping / monkey-patching machinery against
the range of real-world Python callable shapes (functions, methods, generators,
coroutines, async generators, context managers, decorator stacks, and
version-specific syntax). Every test wraps a readable callable with each of the
four mechanisms and asserts its explicit expected behaviour — a wrapped callable
must be indistinguishable from the original.

Layout:
  * ``mechanisms.py`` — the four wrap adapters (internal_wrap, tracer_wrap, wrapt,
    wrapping_context).
  * ``_harness.py`` — the ``mechanisms`` / ``mechanisms_param`` parametrization and
    async run helpers.
  * ``conftest.py`` — version-gated collection (collect_ignore) for the
    ``test_*_py<NN>.py`` files.
  * ``test_*.py`` — tests grouped by callable category.

See ``README.md`` for the full layout, the known-failure (xfail) table, and how
to run the suite.
"""
