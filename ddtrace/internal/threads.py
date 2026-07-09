import sys

from ddtrace.internal.utils import threads as _threads


# Alias this legacy import path to the implementation module so mutable module
# state (e.g. _forking) is shared rather than copied by a star-import.
sys.modules[__name__] = _threads
