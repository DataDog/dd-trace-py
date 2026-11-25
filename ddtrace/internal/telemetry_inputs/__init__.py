"""What telemetry needs from the library it reports for, passed in rather than imported.

Telemetry moves to the ddtrace-internal package, which never imports ddtrace, so it can't read dd-trace-py's
version or code locations itself. dd-trace-py registers them from ``ddtrace/__init__.py`` before it imports
telemetry. Each concern has a ``register_*`` function and a getter, and the first registration wins.

- ``library``: the library's identity.

This package sits outside ``ddtrace.internal.telemetry`` on purpose, and imports nothing from ddtrace: importing
anything under the telemetry package runs its ``__init__``, which builds the writer and reports the configurations
at import time, and both read these inputs.
"""
