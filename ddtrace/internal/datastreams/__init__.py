from ddtrace import config

from . import kafka  # noqa:F401


_processor = None


def data_streams_processor():
    from . import processor

    global _processor
    if config.data_streams_enabled and not _processor:
        _processor = processor.DataStreamsProcessor()

    return _processor
