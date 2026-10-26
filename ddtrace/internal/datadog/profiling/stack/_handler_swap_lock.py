"""Shared lock for stack SIGSEGV/SIGBUS handler pause/swap/resume.

Sampler::pause is not nested: a second resume clears the first pause. Faulthandler
and crashtracking both pause around handler swaps, so they must share one lock
for the whole pause/uninstall/reinstall/resume transaction.
"""

from __future__ import annotations

from ddtrace.internal.forksafe import Lock


# forksafe.Lock resets after fork so a child does not inherit a held mutex.
handler_swap_lock = Lock()
