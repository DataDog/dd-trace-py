import os
from typing import Any
from typing import Callable
from typing import Sequence

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
        dispatch: Callable[[Sequence[Any]], None],
        name: str,
        enabled_products: Callable[[], list[str]],
    ) -> None:
        super().__init__(interval=0, autorestart=False)  # interval handled by wait_for_change
        self._reader = reader
        self._dispatch = dispatch
        self._name = name
        # Names of the products this child subscribes to. The native reader uses
        # this to withhold configs for not-yet-enabled products from its diff, so
        # they are re-delivered once the product is enabled (in-product enablement).
        self._enabled_products = enabled_products
        # Wake at least twice per agent poll interval for periodic() liveness
        # Note: on linux a futex is used for more timely updates.
        self._timeout_ms = max(1, int(config._remote_config_poll_interval * 1000 / 2))

    def periodic(self):
        try:
            # Read first so a freshly-forked child observes the inherited snapshot
            # immediately, then block until the next publish/notify (or timeout).
            records = self._reader.read(self._enabled_products())
            self._dispatch(records)
            self._reader.wait_for_change(self._timeout_ms)
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
