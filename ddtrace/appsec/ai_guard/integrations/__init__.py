"""Deprecated location for the AI Guard third-party integrations.

The integration modules moved to ``ddtrace.aiguard.integrations``. The
submodules under this package (``litellm``, ``strands``) are kept as
lightweight shims that re-export their public symbols from the new location
and emit a ``DDTraceDeprecationWarning`` on first access. They will be removed
in 5.0.0.
"""
