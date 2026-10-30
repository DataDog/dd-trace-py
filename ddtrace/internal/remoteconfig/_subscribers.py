from collections.abc import Sequence
import os
from typing import Any
from typing import Callable
from typing import Optional

from ddtrace import config
from ddtrace.internal.logger import get_logger
from ddtrace.internal.periodic import PeriodicService


log = get_logger(__name__)


class RemoteConfigSubscriber(PeriodicService):
    """Child-process consumer of the native Remote Config SHM distribution.

    Forked children do not poll the agent; instead they read the configuration
    snapshots the master process publishes to shared memory (via the native reader,
    which diffs successive snapshots into add/update/remove changes) and dispatches
    them to the registered product callbacks.
    """

    def __init__(
        self,
        reader: Any,
        dispatch: Callable[[Optional[Sequence[Any]], Sequence[str]], None],
        name: str,
        enabled_products: Callable[[], list[str]],
    ) -> None:
        # Poll at twice the agent poll rate. The wait is the PeriodicThread's own,
        # so stop() and the before-fork hook interrupt it at once. A blocking wait
        # inside periodic() would hold up every fork from this process until it
        # timed out.
        super().__init__(
            interval=config._remote_config_poll_interval / 2,
            no_wait_at_start=True,
            autorestart=False,
        )
        self._reader = reader
        self._dispatch = dispatch
        self._name = name
        # Names of the products this child subscribes to. The native reader uses
        # this to withhold configs for not-yet-enabled products from its diff, so
        # they are re-delivered once the product is enabled (in-product enablement).
        self._enabled_products = enabled_products

    def periodic(self):
        try:
            # The first call runs at start, so a freshly-forked child observes the
            # inherited snapshot immediately. The records are reconciled against
            # the products they were read for.
            enabled = self._enabled_products()
            records = self._reader.read(enabled)
            self._dispatch(records, enabled)
        except Exception:
            log.error(
                "[PID %d | PPID %d] %s error consuming remote config",
                os.getpid(),
                os.getppid(),
                self,
                exc_info=True,
            )

    def __str__(self):
        return f"Subscriber {self._name}"
