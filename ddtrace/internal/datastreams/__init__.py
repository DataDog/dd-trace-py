from ddtrace import config
from ddtrace.settings._agent import config as agent_config

from ...internal.utils.importlib import require_modules


required_modules = ["confluent_kafka", "botocore", "kombu"]
_processor = None

if config._data_streams_enabled:
    with require_modules(required_modules) as missing_modules:
        if "confluent_kafka" not in missing_modules:
            from . import kafka  # noqa:F401
        if "botocore" not in missing_modules:
            from . import botocore  # noqa:F401
        if "kombu" not in missing_modules:
            from . import kombu  # noqa:F401


def data_streams_processor():
    global _processor
    if config._data_streams_enabled and not _processor:
        from . import processor

        _processor = processor.DataStreamsProcessor(agent_config.trace_agent_url)

    return _processor
