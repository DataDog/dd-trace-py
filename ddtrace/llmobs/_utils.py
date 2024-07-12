from typing import Optional

import ddtrace
from ddtrace import Span
from ddtrace import config
from ddtrace.ext import SpanTypes
from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._constants import LANGCHAIN_APM_SPAN_NAME
from ddtrace.llmobs._constants import ML_APP
from ddtrace.llmobs._constants import OPENAI_APM_SPAN_NAME
from ddtrace.llmobs._constants import PARENT_ID_KEY
from ddtrace.llmobs._constants import PROPAGATED_PARENT_ID_KEY
from ddtrace.llmobs._constants import SESSION_ID


log = get_logger(__name__)


def _get_nearest_llmobs_ancestor(span: Span) -> Optional[Span]:
    """Return the nearest LLMObs-type ancestor span of a given span."""
    parent = span._parent
    while parent:
        if parent.span_type == SpanTypes.LLM:
            return parent
        parent = parent._parent
    return None


def _get_llmobs_parent_id(span: Span) -> Optional[str]:
    """Return the span ID of the nearest LLMObs-type span in the span's ancestor tree.
    In priority order: manually set parent ID tag, nearest LLMObs ancestor, local root's propagated parent ID tag.
    """
    if span.get_tag(PARENT_ID_KEY):
        return span.get_tag(PARENT_ID_KEY)
    nearest_llmobs_ancestor = _get_nearest_llmobs_ancestor(span)
    if nearest_llmobs_ancestor:
        return str(nearest_llmobs_ancestor.span_id)
    return span.get_tag(PROPAGATED_PARENT_ID_KEY)


def _get_span_name(span: Span) -> str:
    if span.name == LANGCHAIN_APM_SPAN_NAME and span.resource != "":
        return span.resource
    elif span.name == OPENAI_APM_SPAN_NAME and span.resource != "":
        return "openai.{}".format(span.resource)
    return span.name


def _get_ml_app(span: Span) -> str:
    """
    Return the ML app name for a given span, by checking the span's nearest LLMObs span ancestor.
    Default to the global config LLMObs ML app name otherwise.
    """
    ml_app = span.get_tag(ML_APP)
    if ml_app:
        return ml_app
    nearest_llmobs_ancestor = _get_nearest_llmobs_ancestor(span)
    if nearest_llmobs_ancestor:
        ml_app = nearest_llmobs_ancestor.get_tag(ML_APP)
    return ml_app or config._llmobs_ml_app or "unknown-ml-app"


def _get_session_id(span: Span) -> str:
    """
    Return the session ID for a given span, by checking the span's nearest LLMObs span ancestor.
    Default to the span's trace ID.
    """
    session_id = span.get_tag(SESSION_ID)
    if session_id:
        return session_id
    nearest_llmobs_ancestor = _get_nearest_llmobs_ancestor(span)
    if nearest_llmobs_ancestor:
        session_id = nearest_llmobs_ancestor.get_tag(SESSION_ID)
    return session_id or "{:x}".format(span.trace_id)


def _inject_llmobs_parent_id(span_context):
    """Inject the LLMObs parent ID into the span context for reconnecting distributed LLMObs traces."""
    span = ddtrace.tracer.current_span()
    if span is None:
        log.warning("No active span to inject LLMObs parent ID info.")
        return
    if span.context is not span_context:
        log.warning("The current active span and span_context do not match. Not injecting LLMObs parent ID.")
        return

    if span.span_type == SpanTypes.LLM:
        llmobs_parent_id = str(span.span_id)
    else:
        llmobs_parent_id = _get_llmobs_parent_id(span)
    span_context._meta[PROPAGATED_PARENT_ID_KEY] = llmobs_parent_id or "undefined"


def _unserializable_default_repr(obj):
    default_repr = "[Unserializable object: {}]".format(repr(obj))
    log.warning("I/O object is not JSON serializable. Defaulting to placeholder value instead.")
    return default_repr
