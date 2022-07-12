import time
from typing import Any
from typing import Callable
from typing import Dict
from typing import Optional

from ddtrace.debugging._config import config
from ddtrace.debugging._probe.model import Probe
from ddtrace.debugging._remoteconfig import DebuggingRCV07
from ddtrace.internal.logger import get_logger
from ddtrace.internal.periodic import PeriodicService


log = get_logger(__name__)


class ProbePollerEvent(object):
    NEW_PROBES = 0
    DELETED_PROBES = 1
    MODIFIED_PROBES = 2
    STATUS_UPDATE = 3


ProbePollerEventType = int


def probe_modified(reference, probe):
    # type: (Probe, Probe) -> bool
    # DEV: Probes are immutable modulo the active state. Hence we expect
    # probes with the same ID to differ up to this property for now.
    return probe.active != reference.active


class ProbePoller(PeriodicService):
    """Poll RCM for new probe configurations.

    This also keeps tracks of all seen probes to determine whether they are new,
    deleted or modified. The corresponding events are then emitted to the
    callback. Furthermore, probe status updates are emitted periodically to save
    on having to create a dedicated thread.
    """

    def __init__(self, remoteconfig, callback, interval=None):
        # type: (DebuggingRCV07, Callable[[ProbePollerEventType, Any], None], Optional[float]) -> None
        super(ProbePoller, self).__init__(interval if interval is not None else config.poll_interval)

        self._rc = remoteconfig
        self._callback = callback
        self._probes = {}  # type: Dict[str, Probe]

        self._next_status_update_timestamp()

        log.debug("Probe poller service initialized (interval: %d)", self._interval)

    def _next_status_update_timestamp(self):
        self._status_timestamp = time.time() + config.diagnostic_interval

    def periodic(self):
        # type: () -> None

        # DEV: We emit a status update event here to avoid having to spawn a
        # separate thread for this.
        if time.time() > self._status_timestamp:
            self._callback(ProbePollerEvent.STATUS_UPDATE, self._probes)
            self._next_status_update_timestamp()

        probes = self._rc.get_probes()
        if probes is None:
            return

        current_probes = {_.probe_id: _ for _ in probes}

        new_probes = [current_probes[_] for _ in current_probes if _ not in self._probes]
        deleted_probes = [self._probes[_] for _ in self._probes if _ not in current_probes]
        modified_probes = [
            current_probes[_]
            for _ in current_probes
            if _ in self._probes and probe_modified(current_probes[_], self._probes[_])
        ]

        if deleted_probes:
            self._callback(ProbePollerEvent.DELETED_PROBES, deleted_probes)
        if modified_probes:
            self._callback(ProbePollerEvent.MODIFIED_PROBES, modified_probes)
        if new_probes:
            self._callback(ProbePollerEvent.NEW_PROBES, new_probes)

        self._probes = current_probes
