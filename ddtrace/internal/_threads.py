"""Temporary alias of ``ddtrace.internal.native._threads``, kept for the importers not yet moved to the new path.

It goes away in the follow-up that updates them.
"""

import sys
from typing import TYPE_CHECKING

from ddtrace.internal.native import _threads


if TYPE_CHECKING:
    from ddtrace.internal.native._threads import *  # noqa: F403

sys.modules[__name__] = _threads
