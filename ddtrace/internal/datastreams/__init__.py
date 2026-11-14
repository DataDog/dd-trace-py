from __future__ import annotations

import importlib
from typing import TYPE_CHECKING
from typing import Any
from typing import Callable
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
_pipelines_modules = {"confluent_kafka": "kafka", "aiokafka": "aiokafka"}
_processor: Optional[DataStreamsProcessor] = None

if config._data_streams_enabled:
    with require_modules(list(required_module_to_integration.keys())) as missing_modules:
        path_f = "ddtrace.internal.datastreams.%s"
        for integrated_library, integration_module in required_module_to_integration.items():
            if integrated_library not in missing_modules:
                importlib.import_module(path_f % (integration_module,))
elif config._data_streams_pipelines_enabled:
    # Pipelines-only: register handlers lazily, when the client library is imported,
    # so `import ddtrace` stays free of core and third-party imports.
    from ddtrace.internal.module import ModuleWatchdog

    def _import_pipelines_module(integration: str) -> Callable[[Any], None]:
        def _hook(_module: Any) -> None:
            importlib.import_module("ddtrace.internal.datastreams." + integration)

        return _hook

    for _library, _integration in _pipelines_modules.items():
        ModuleWatchdog.register_module_hook(_library, _import_pipelines_module(_integration))


def data_streams_processor(reset: bool = False) -> Optional[DataStreamsProcessor]:
    global _processor
    if config._data_streams_enabled and (not _processor or reset):
        from . import processor

        _processor = processor.DataStreamsProcessor()

    return _processor
