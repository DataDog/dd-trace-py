"""
Filesystem instrumentation publishes file-open attempts for security inspection.
App and API Protection installs it automatically when required. It does not create spans.

It can also be installed explicitly with ``ddtrace.patch(filesystem=True)``.
"""
