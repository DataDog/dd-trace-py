# Backwards-compatibility shim: the pytest plugin moved to
# ddtrace.testing.internal.pytest but stale venvs may still have
# entry-point metadata pointing here.
from ddtrace.testing.internal.pytest.entry_point import *  # noqa: F401,F403
