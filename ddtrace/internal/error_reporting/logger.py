import logging

from ddtrace._trace.span import SpanEvent


logging.basicConfig(
    filename="log.log",
    filemode="a",
    format="%(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.ERROR,
)
logger = logging.getLogger("handledExceptionsLogger")


def exception(_: SpanEvent):
    logger.error("exception")
    # attrs = event.attributes
    # logger.error(f'{attrs["exception.type"]}({attrs["exception.message"]}) - {attrs["exception.stacktrace"]}')
