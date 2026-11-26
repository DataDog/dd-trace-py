from __future__ import annotations

import importlib
from typing import TYPE_CHECKING
from typing import Optional

from ddtrace.internal.settings._config import config

from ...internal.utils.importlib import require_modules


if TYPE_CHECKING:
    from .processor import DataStreamsProcessor


required_module_to_integration = {
    "confluent_kafka": "kafka",
    "botocore": "botocore",
    "kombu": "kombu",
    "aiokafka": "aiokafka",
    "google.cloud.pubsub_v1": "google_cloud_pubsub",
}
_processor: Optional[DataStreamsProcessor] = None

if config._data_streams_enabled:
    with require_modules(list(required_module_to_integration.keys())) as missing_modules:
        path_f = "ddtrace.internal.datastreams.%s"
        for integrated_library, integration_module in required_module_to_integration.items():
            if integrated_library not in missing_modules:
                importlib.import_module(path_f % (integration_module,))


def _create_processor() -> DataStreamsProcessor:
    # Imported here so that processes without data streams monitoring do not load the
    # processor and its dependencies (startup cost).
    from . import processor

    return processor.DataStreamsProcessor()


if config._data_streams_enabled:
    # Create, and so start, the processor when ddtrace is imported, rather than on the first
    # checkpoint: an application thread must not start ddtrace threads, as it might be holding
    # locks of its own.
    _processor = _create_processor()


def data_streams_processor(reset: bool = False) -> Optional[DataStreamsProcessor]:
    """Return the data streams processor, which exists when data streams monitoring is enabled.

    reset creates a new processor, e.g. for tests that enable data streams monitoring at runtime.
    """
    global _processor
    if reset and config._data_streams_enabled:
        _processor = _create_processor()

    return _processor
