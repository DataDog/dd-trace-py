"""What telemetry needs from the library it reports for, passed in rather than imported.

Telemetry moves to the ddtrace-internal package, which never imports ddtrace, so it can't read dd-trace-py's
version, code locations or product settings itself. dd-trace-py registers them from ``ddtrace/__init__.py`` before
it imports telemetry. Each submodule holds one concern, with ``register_*`` functions and getters. The first
registration wins, and the documented defaults apply when nothing registers:

- ``library``: the library's identity, the code it owns and how to name its integrations.
- ``configurations``: configuration objects to report when telemetry is imported.
- ``dependencies``: whether to collect full metadata for each dependency.
- ``endpoints``: whether to collect API endpoints and how many to send in a message.

This package sits outside ``ddtrace.internal.telemetry`` on purpose, and its modules import nothing from ddtrace:
importing anything under the telemetry package runs its ``__init__``, which builds the writer and reports the
configurations at import time, and both read these inputs.

This ``__init__`` imports nothing, so importing one submodule loads no other.
"""
