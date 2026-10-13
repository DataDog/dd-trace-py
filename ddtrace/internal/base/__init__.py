"""The pure-Python base layer of ``ddtrace.internal``.

Modules under this package must not import the native extension
(``ddtrace.internal.native``) or any non-base ``ddtrace.internal`` module;
the native-coupled layer may freely import from here, never the reverse.
See docs/ddtrace-internal-layering.md in datadog-apm-python.
"""
