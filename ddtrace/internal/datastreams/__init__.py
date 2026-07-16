import importlib

from ddtrace.internal.settings._config import config

from ...internal.utils.importlib import require_modules


required_module_to_integration = {
    "confluent_kafka": "kafka",
    "botocore": "botocore",
    "kombu": "kombu",
    "aiokafka": "aiokafka",
    "google.cloud.pubsub_v1": "google_cloud_pubsub",
}
_processor = None

if config._data_streams_enabled:
    with require_modules(list(required_module_to_integration.keys())) as missing_modules:
        path_f = "ddtrace.internal.datastreams.%s"
        for integrated_library, integration_module in required_module_to_integration.items():
            if integrated_library not in missing_modules:
                importlib.import_module(path_f % (integration_module,))


def data_streams_processor(reset=False):
    global _processor
    if config._data_streams_enabled and (not _processor or reset):
        from . import processor

        _processor = processor.DataStreamsProcessor()

    return _processor
