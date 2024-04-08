from datetime import datetime
import os
from typing import Callable  # noqa:F401
from typing import Optional  # noqa:F401

from ddtrace import Tracer  # noqa:F401
from ddtrace.internal.logger import get_logger
from ddtrace.internal.periodic import PeriodicService
from ddtrace.internal.remoteconfig._connectors import PublisherSubscriberConnector  # noqa:F401
from ddtrace.internal.remoteconfig._connectors import SharedDataType  # noqa:F401
from ddtrace.internal.remoteconfig.utils import get_poll_interval_seconds


log = get_logger(__name__)

STALE_TRACER_FLARE_NUM_MINS: int = 20


class RemoteConfigSubscriber(PeriodicService):
    def __init__(self, data_connector, callback, name):
        # type: (PublisherSubscriberConnector, Callable, str) -> None
        super().__init__(get_poll_interval_seconds() / 2)

        self._data_connector = data_connector
        self._callback = callback
        self._name = name

        log.debug("[PID %d] %s initialized", os.getpid(), self)

    def _exec_callback(self, data, test_tracer=None):
        # type: (SharedDataType, Optional[Tracer]) -> None
        if data:
            log.debug("[PID %d] %s _exec_callback: %s", os.getpid(), self, str(data)[:50])
            self._callback(data, test_tracer=test_tracer)

    def _get_data_from_connector_and_exec(self, test_tracer=None):
        # type: (Optional[Tracer]) -> None
        data = self._data_connector.read()
        self._exec_callback(data, test_tracer=test_tracer)

    def periodic(self):
        try:
            log.debug("[PID %d | PPID %d] %s is getting data", os.getpid(), os.getppid(), self)
            self._get_data_from_connector_and_exec()
            log.debug("[PID %d | PPID %d] %s got data", os.getpid(), os.getppid(), self)
        except Exception:
            log.error("[PID %d | PPID %d] %s while getting data", os.getpid(), os.getppid(), self, exc_info=True)

    def force_restart(self, join=False):
        self.stop()
        if join:
            self.join()
        self.start()
        log.debug("[PID %d | PPID %d] %s restarted", os.getpid(), os.getppid(), self)

    def __str__(self):
        return f"Subscriber {self._name}"


class TracerFlareSubscriber(RemoteConfigSubscriber):
    def __init__(
        self,
        data_connector: PublisherSubscriberConnector,
        callback: Callable,
        stale_flare_age: int = STALE_TRACER_FLARE_NUM_MINS,
    ):
        super().__init__(data_connector, callback, "TracerFlareConfig")
        self._current_request_start: Optional[datetime] = None
        self._stale_tracer_flare_num_mins = stale_flare_age

    def _get_current_request_start(self) -> Optional[datetime]:
        return self._current_request_start

    def _set_stale_tracer_flare_num_mins(self, mins: int):
        self._stale_tracer_flare_num_mins = mins

    def _has_stale_flare(self) -> bool:
        if self._current_request_start:
            curr = datetime.now()
            flare_age = (curr - self._current_request_start).total_seconds()
            stale_age = self._stale_tracer_flare_num_mins * 60
            return flare_age >= stale_age
        return False

    def _get_data_from_connector_and_exec(self):
        # Check for stale tracer flare job
        if self._current_request_start is not None and self._has_stale_flare():
            log.debug(
                "Tracer flare request started at %s is stale, reverting "
                "logger configurations and cleaning up resources now",
                self._current_request_start,
            )
            self._current_request_start = None
            self._callback({}, True)
            return

        data = self._data_connector.read()
        product_type = data.get("metadata", [{}])[0].get("product_name")
        if product_type == "AGENT_CONFIG":
            # We will only process one tracer flare request at a time
            if self._current_request_start is not None:
                log.warning(
                    "There is already a tracer flare job started at %s. Skipping new request.",
                    str(self._current_request_start),
                )
                return
            self._current_request_start = datetime.now()
        elif product_type == "AGENT_TASK":
            # Possible edge case where we missed the AGENT_CONFIG product
            # In this case we won't have anything to send, so we log and do nothing
            if self._current_request_start is None:
                log.warning("There is no tracer flare job to complete. Skipping new request.")
                return
            self._current_request_start = None
        else:
            log.warning("Unexpected tracer flare product type %r", product_type)
            return
        log.debug("[PID %d] %s _exec_callback: %s", os.getpid(), self, str(data)[:50])
        self._callback(data)
