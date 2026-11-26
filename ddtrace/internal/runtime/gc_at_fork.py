"""The GC statistics of the process when it was forked.

A forked child inherits the GC statistics of its parent, and reports those since the fork in the OTLP runtime metrics.
The runtime metrics record them when they are enabled, as the OTLP runtime metrics are only loaded when OpenTelemetry is
imported, possibly after the fork, e.g. in the workers of a server that forks before loading the application.
"""

import gc

from .. import forksafe


stats: list[dict[str, int]] = []
_recorded = False


def _record() -> None:
    global stats

    stats = gc.get_stats()


def record() -> None:
    """Record the GC statistics when the process forks."""
    global _recorded

    if not _recorded:
        forksafe.register(_record)
        _recorded = True
