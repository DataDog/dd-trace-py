"""
Module to provide a Datadog log writer to the OpenAI integration.
"""
import datetime
import os
import typing

from ddtrace import config
from ddtrace import tracer
from ddtrace.internal.hostname import get_hostname

from ._logging import V2LogWriter


if typing.TYPE_CHECKING:
    from typing import Dict
    from typing import List

    from ddtrace import Span


_logs_writer = None


def start(site, api_key):
    global _logs_writer

    _logs_writer = V2LogWriter(
        site=site,
        api_key=api_key,
        interval=1.0,
        timeout=2.0,
    )
    _logs_writer.start()


def log(span, level, msg, tags, attrs):
    # type: (Span, str, str, List[str], Dict[str, str]) -> None
    global _logs_writer

    curspan = tracer.current_span()  # TODO: pass this
    timestamp = datetime.datetime.now().isoformat()

    log = {
        "message": "%s %s" % (timestamp, msg),
        "hostname": os.getenv("DD_HOSTNAME", get_hostname()),  # TODO: move DD_HOSTNAME logic to get_hostname()
        "ddsource": "openai",  # TODO: should be OpenAI?
        "service": span.service,  # TODO: use same as the span
        "status": level,
    }
    # TODO: these tags should come from caller
    if config.env:
        tags.append("env:%s" % config.env)
    if config.version:
        tags.append("version:%s" % config.version)
    log["ddtags"] = ",".join(t for t in tags)

    if curspan is not None:
        log["dd.trace_id"] = str(curspan.trace_id)
        log["dd.span_id"] = str(curspan.span_id)

    # Update the logs with any additional attributes the caller has provided.
    log.update(attrs)
    _logs_writer.enqueue(log)
