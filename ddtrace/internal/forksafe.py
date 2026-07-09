import sys

from ddtrace.internal.utils import forksafe as _forksafe


# Alias this legacy import path to the implementation module so mutable module
# state (e.g. _forked) is shared rather than copied by a star-import.
sys.modules[__name__] = _forksafe
