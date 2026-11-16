from __future__ import annotations

import importlib
from typing import TYPE_CHECKING
from typing import Optional

from ddtrace.internal.forksafe import Lock
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings._config import config

from ...internal.utils.importlib import require_modules


if TYPE_CHECKING:
    from .processor import DataStreamsProcessor

log = get_logger(__name__)

required_module_to_integration = {
    "confluent_kafka": "kafka",
    "botocore": "botocore",
    "kombu": "kombu",
    "aiokafka": "aiokafka",
    "google.cloud.pubsub_v1": "google_cloud_pubsub",
}
_processor: Optional[DataStreamsProcessor] = None
# Serializes processor creation and RC stop, so neither orphans a running processor.
# Fork-safe: a child must not inherit a lock held by a parent thread.
_processor_lock = Lock()


def _import_integration_modules() -> None:
    """Import the per-system hook modules for every installed messaging client."""
    with require_modules(list(required_module_to_integration.keys())) as missing_modules:
        path_f = "ddtrace.internal.datastreams.%s"
        for integrated_library, integration_module in required_module_to_integration.items():
            if integrated_library not in missing_modules:
                importlib.import_module(path_f % (integration_module,))


if config._data_streams_enabled:
    _import_integration_modules()


def data_streams_processor(reset: bool = False) -> Optional[DataStreamsProcessor]:
    global _processor
    if config._data_streams_enabled and (not _processor or reset):
        with _processor_lock:
            # Recheck: an RC disable may have landed while waiting for the lock.
            if config._data_streams_enabled and (not _processor or reset):
                from . import processor

                _processor = processor.DataStreamsProcessor()
    # A stale processor with the setting off (e.g. a fork racing a remote-config
    # disable) must not keep reporting: the handlers treat None as inactive.
    return _processor if config._data_streams_enabled else None


def start_data_streams_processor() -> None:
    """Enable classic DSM at runtime from remote config. Never raises.

    Imports the hook modules skipped at startup when DSM was off, then creates the processor.
    """
    try:
        _import_integration_modules()
        data_streams_processor()
    except Exception:  # noqa: BLE001 - never raise into the RC callback
        log.debug("failed to enable data streams monitoring at runtime", exc_info=True)


def stop_data_streams_processor() -> None:
    """Disable classic DSM at runtime from remote config. Never raises.

    The final flush retries past the shutdown timeout, so this can block the
    remote config worker thread for a few seconds.
    """
    global _processor
    try:
        # Swap under the lock so a processor being constructed is not missed;
        # shut down outside it since the final flush blocks.
        with _processor_lock:
            processor = _processor
            _processor = None
        if processor is not None:
            from .processor import SHUTDOWN_TIMEOUT

            processor.shutdown(SHUTDOWN_TIMEOUT)
    except Exception:  # noqa: BLE001 - never raise into the RC callback
        log.debug("failed to disable data streams monitoring at runtime", exc_info=True)
