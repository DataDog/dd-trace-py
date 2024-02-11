import time

from ddtrace.internal import core
from ddtrace.internal.accupath.pathway_context import _get_current_pathway_context, _inject_request_pathway_context
from ddtrace.internal.accupath.processor import submit_metrics
from ddtrace.internal.logger import get_logger

log = get_logger('accupath')


def _enabled():
    # Can later read from an env var
    return True


def handle_request_in(headers, *args, **kwargs):
    try:
        current_pathway_context = _get_current_pathway_context(headers=headers)
        current_pathway_context.checkpoint("request_in", time.time_ns())
        log.debug(f"AccuPath - request_in {current_pathway_context.uid}")
    except Exception as e:
        log.debug("Error in handle_request_in", exc_info=True)


def handle_request_out(headers, *args, **kwargs):
    try:
        current_pathway_context = _get_current_pathway_context()
        current_pathway_context.checkpoint("request_out", time.time_ns())
        log.debug(f"AccuPath - request_out {current_pathway_context.uid}")
        _inject_request_pathway_context(headers)
    except Exception as e:
        log.debug("Error in handle_request_in", exc_info=True)

def handle_response_in(status_code, *args, **kwargs):
    try:
        current_pathway_context = _get_current_pathway_context()
        current_pathway_context.checkpoint("response_in", time.time_ns())
        log.debug(f"AccuPath - response_in {current_pathway_context.uid}")
        current_pathway_context.success = (status_code < 400)
        submit_metrics()
    except Exception as e:
        log.debug("Error in handle_request_in", exc_info=True)


def handle_response_out(headers, resource_name, operation_name, status, *args, **kwargs):
    try:
        current_pathway_context = _get_current_pathway_context()
        current_pathway_context.checkpoint("response_out", time.time_ns())
        log.debug(f"AccuPath - response_out {current_pathway_context.uid}")
        submit_metrics()
    except Exception as e:
        log.debug("Error in handle_request_in", exc_info=True)

def handle_request_start(resource, operation, *args, **kwargs):
    try:
        current_pathway_context = _get_current_pathway_context()
        current_pathway_context.resource_name = resource
        current_pathway_context.operation_name = operation
        log.debug(f"AccuPath - request_start {current_pathway_context.uid}")
    except Exception as e:
        log.debug("Error in handle_request_in", exc_info=True)

if _enabled():
    core.on("http.request.header.extraction", handle_request_in)
    core.on("http.request.header.injection", handle_request_out)
    core.on("http.response.header.extraction", handle_response_in)
    core.on("http.response.header.injection", handle_response_out)
    core.on("http.request.start", handle_request_start)