"""Wrapping compatibility suite for dd-trace-py function wrapping.

Hand-written tests that exercise the wrapping / monkey-patching machinery against
the range of real-world Python callable shapes (functions, methods, generators,
coroutines, async generators, context managers, decorator stacks, and
version-specific syntax). Every test wraps a readable callable with each of the
four mechanisms and asserts its explicit expected behaviour — a wrapped callable
must be indistinguishable from the original.

Layout:
  * ``mechanisms.py`` — the four wrap adapters (internal_wrap, tracer_wrap, wrapt,
    wrapping_context), collected into ``ALL_MECHANISMS``, plus the
    ``xfail_mechanism`` helper for declaring known per-mechanism failures.
  * ``conftest.py`` — the ``mech`` fixture that runs each test over all four
    mechanisms, and version-gated collection (collect_ignore) for the
    ``test_*_py<NN>.py`` files.
  * ``_harness.py`` — async run helpers (``run``, ``aiterate``) and ``wraps_deco``.
  * ``test_*.py`` — tests grouped by callable category. A known failure is declared
    at the test site with ``@xfail_mechanism("<mechanism>", reason=...)``.
"""
