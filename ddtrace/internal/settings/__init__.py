"""
Compatibility shims for legacy imports that used the
``ddtrace.internal.settings`` namespace. The real implementations now live
under ``ddtrace.settings``; this package just keeps those old paths
importable for compiled extensions.
"""
