"""Shared lock for SIGSEGV/SIGBUS handler pause/swap/resume.

Faulthandler and crashtracking both pause around swaps; Sampler::pause is not
nested, so they share one lock for the whole transaction.
"""

from __future__ import annotations

from ddtrace.internal.forksafe import Lock


# forksafe.Lock resets after fork so a child does not inherit a held mutex.
handler_swap_lock = Lock()
