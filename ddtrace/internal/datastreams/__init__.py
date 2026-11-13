from __future__ import annotations

import importlib
from typing import TYPE_CHECKING
from typing import Optional

from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings._config import config
from ddtrace.internal.threads import Lock

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
# Serializes processor creation: two threads racing on the first checkpoint
# would otherwise each construct a processor, orphaning a running one.
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
            if not _processor or reset:
                from . import processor

                _processor = processor.DataStreamsProcessor()
    # A stale processor with the setting off (e.g. a fork racing a remote-config
    # disable) must not keep reporting: the handlers treat None as inactive.
    return _processor if config._data_streams_enabled else None


def start_data_streams_processor() -> None:
    """Enable classic DSM at runtime (remote config, DSM2-599). Never raises.

    Imports the per-system hook modules (skipped at import time when DSM was
    off at startup) and creates the processor.
    """
    try:
        _import_integration_modules()
        if _processor is None:
            data_streams_processor()
    except Exception:  # noqa: BLE001 - never raise into the RC callback
        log.debug("failed to enable data streams monitoring at runtime", exc_info=True)


def stop_data_streams_processor() -> None:
    """Disable classic DSM at runtime (remote config, DSM2-599). Never raises.

    Shuts the processor down (flushing what it holds; the retries of that flush
    are not bounded by the timeout, so this can block the shared RC worker
    thread for a few seconds) and drops the global so the hook handlers go back
    to their no-op path. A fork landing between the setting flip and the
    processor drop leaves the child an inherited processor; the disabled-setting
    check in data_streams_processor() makes the child's hooks go inactive again.
    """
    global _processor
    processor = _processor
    _processor = None
    try:
        if processor is not None:
            from .processor import SHUTDOWN_TIMEOUT

            processor.shutdown(SHUTDOWN_TIMEOUT)
    except Exception:  # noqa: BLE001 - never raise into the RC callback
        log.debug("failed to disable data streams monitoring at runtime", exc_info=True)
