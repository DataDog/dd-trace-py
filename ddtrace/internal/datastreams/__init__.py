from ddtrace import config
from ddtrace.internal.atexit import register_on_exit_signal
from ddtrace.trace import tracer

from ...internal.utils.importlib import require_modules


required_modules = ["confluent_kafka", "botocore", "kombu", "aiokafka"]
_processor = None

if config._data_streams_enabled:
    with require_modules(required_modules) as missing_modules:
        if "confluent_kafka" not in missing_modules:
            from . import kafka  # noqa:F401
        if "aiokafka" not in missing_modules:
            from . import aiokafka  # noqa:F401
        if "botocore" not in missing_modules:
            from . import botocore  # noqa:F401
        if "kombu" not in missing_modules:
            from . import kombu  # noqa:F401


def data_streams_processor(reset=False):
    global _processor
    if config._data_streams_enabled and (not _processor or reset):
        from . import processor

        _processor = processor.DataStreamsProcessor()
        register_on_exit_signal(tracer._atexit)

    return _processor
