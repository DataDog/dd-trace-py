from datetime import timezone
import json
import sys
from typing import Any
from typing import Optional
from typing import cast

from ddtrace._trace.span import Span
from ddtrace.constants import ERROR_MSG
from ddtrace.constants import ERROR_TYPE
from ddtrace.ext import SpanTypes
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.formats import format_trace_id
from ddtrace.llmobs._constants import LLMOBS_STRUCT
from ddtrace.llmobs._integrations.bedrock_utils import parse_model_id
from ddtrace.llmobs._utils import _annotate_llmobs_span_data
from ddtrace.llmobs._utils import _get_llmobs_data_metastruct
from ddtrace.llmobs._utils import get_llmobs_ml_app
from ddtrace.llmobs._utils import get_llmobs_session_id
from ddtrace.llmobs._utils import get_llmobs_trace_id
from ddtrace.llmobs._utils import safe_json
from ddtrace.llmobs.types import Message
from ddtrace.trace import tracer


log = get_logger(__name__)


INTEGRATION_TAG = {LLMOBS_STRUCT.INTEGRATION: "bedrock_agents"}

# Used when AWS event has no duration (e.g. rationale); 1 ms matches pre-migration default.
DEFAULT_SPAN_DURATION_NS = 1_000_000


class BedrockGuardrailTriggeredException(Exception):
    """Custom exception to represent Bedrock Agent Guardrail Triggered trace events."""

    pass


class BedrockFailureException(Exception):
    """Custom exception to represent Bedrock Agent Failure trace events."""

    pass


def _build_step_span(
    span_name: str,
    root_span: Span,
    parent: Span,
    span_kind: str,
    start_ns: Optional[int] = None,
    metadata: Optional[dict[str, Any]] = None,
    input_val: Optional[Any] = None,
    output_val: Optional[Any] = None,
) -> Span:
    # activate=False so the post-stream reconstructed spans don't pollute the active context.
    span = tracer.start_span(span_name, child_of=parent, span_type=SpanTypes.LLM, activate=False)
    span.start_ns = int(start_ns if start_ns is not None else root_span.start_ns)

    # TODO: drop the explicit trace_id once LLMObs trace_id derivation is unified with APM trace_id.
    annotate_kwargs: dict[str, Any] = {
        "kind": span_kind,
        "parent_id": str(parent.span_id),
        "trace_id": get_llmobs_trace_id(root_span) or format_trace_id(root_span.trace_id),
        "ml_app": get_llmobs_ml_app(root_span),
        "tags": INTEGRATION_TAG,
    }
    session_id = get_llmobs_session_id(root_span)
    if session_id:
        annotate_kwargs["session_id"] = session_id
    if metadata is not None:
        annotate_kwargs["metadata"] = metadata
    if input_val is not None:
        annotate_kwargs["input_messages" if span_kind == "llm" else "input_value"] = input_val
    if output_val is not None:
        annotate_kwargs["output_messages" if span_kind == "llm" else "output_value"] = output_val

    _annotate_llmobs_span_data(span, **annotate_kwargs)
    return span


def _max_finish_ns(span: Span) -> int:
    """Return the absolute end time of a finished span in nanoseconds (start_ns + duration_ns)."""
    return int(span.start_ns or 0) + int(span.duration_ns or 0)


def _extract_trace_step_id(bedrock_trace_obj):
    """Extracts the trace step ID from a Bedrock trace object.
    Due to the union structure of bedrock traces (only one key-value pair representing the actual trace object),
    some trace types have the trace step ID in the underlying trace object, while others have it in a nested object.
    """
    trace_part = bedrock_trace_obj.get("trace", {})
    if not trace_part or not isinstance(trace_part, dict) or len(trace_part) != 1:
        return None
    trace_type, trace_part = next(iter(trace_part.items()))
    if not trace_part or not isinstance(trace_part, dict):
        return None
    if "traceId" in trace_part and trace_type in ("customOrchestrationTrace", "failureTrace", "guardrailTrace"):
        return trace_part.get("traceId")
    if len(trace_part) != 1:
        return None
    _, trace_part = next(iter(trace_part.items()))
    return trace_part.get("traceId")


def _extract_trace_type(bedrock_trace_obj):
    """Extracts the first key from a Bedrock trace object, which represents the underlying trace type."""
    trace_part = bedrock_trace_obj.get("trace", {})
    if not trace_part or not isinstance(trace_part, dict) or len(trace_part) != 1:
        return None
    trace_type, _ = next(iter(trace_part.items()))
    return trace_type


def _extract_start_ns(bedrock_trace_obj, root_span):
    start_ns = bedrock_trace_obj.get("eventTime")
    if start_ns:
        start_ns = start_ns.replace(tzinfo=timezone.utc).timestamp() * 1e9
    else:
        start_ns = root_span.start_ns
    return int(start_ns)


def _extract_start_and_duration_from_metadata(bedrock_metadata, root_span):
    """Extracts the start time and duration from the Bedrock trace metadata (non-orchestration trace types)."""
    start_ns = bedrock_metadata.get("startTime")
    if start_ns:
        start_ns = start_ns.replace(tzinfo=timezone.utc).timestamp() * 1e9
    else:
        start_ns = root_span.start_ns
    duration_ns = bedrock_metadata.get("totalTimeMs", 1) * 1e6
    return int(start_ns), int(duration_ns)


def _create_bedrock_trace_step_span(
    trace: dict[str, Any],
    trace_step_id: str,
    root_span: Span,
    step_spans_by_step_id: dict[str, Span],
) -> Span:
    """Return the existing step parent span for ``trace_step_id`` or create a new one."""
    step_span = step_spans_by_step_id.get(trace_step_id)
    if step_span is not None:
        return step_span
    trace_type = _extract_trace_type(trace) or "Bedrock Agent"
    step_span = _build_step_span(
        "{} Step".format(trace_type),
        root_span,
        root_span,
        "workflow",
        start_ns=_extract_start_ns(trace, root_span),
        metadata={"bedrock_trace_id": trace_step_id},
    )
    step_spans_by_step_id[trace_step_id] = step_span
    return step_span


def _propagate_inner_io_to_step_span(step_span: Span, inner_span: Span) -> None:
    # Direct meta_struct copy (bypasses _annotate_llmobs_span_data) so a child llm span's
    # list[Message] input/output is not re-serialized into a JSON value string on the
    # workflow-kind step span. First non-empty input wins; last output wins.
    inner_meta = _get_llmobs_data_metastruct(inner_span).get("meta", {})
    inner_input = inner_meta.get("input") or {}
    inner_output = inner_meta.get("output") or {}
    if not inner_input and not inner_output:
        return

    step_data = _get_llmobs_data_metastruct(step_span)
    step_meta = step_data.setdefault("meta", {})
    step_input = step_meta.get("input") or {}
    if not step_input and inner_input:
        step_meta["input"] = inner_input
    if inner_output:
        step_meta["output"] = inner_output
    step_span._set_struct_tag(LLMOBS_STRUCT.KEY, cast(dict[str, Any], step_data))


def _translate_custom_orchestration_trace(
    trace: dict[str, Any], root_span: Span, parent: Span, current_active_span: Optional[Span], trace_step_id: str
) -> tuple[Optional[Span], bool]:
    custom_orchestration_trace = trace.get("trace", {}).get("customOrchestrationTrace", {})
    start_ns = _extract_start_ns(trace, root_span)
    custom_orchestration_event = custom_orchestration_trace.get("event", {})
    if not custom_orchestration_event or not isinstance(custom_orchestration_event, dict):
        return None, False
    span = _build_step_span(
        span_name="customOrchestration",
        root_span=root_span,
        parent=parent,
        span_kind="task",
        start_ns=start_ns,
        output_val=custom_orchestration_event.get("text", ""),
    )
    return span, False


def _translate_orchestration_trace(
    trace: dict[str, Any], root_span: Span, parent: Span, current_active_span: Optional[Span], trace_step_id: str
) -> tuple[Optional[Span], bool]:
    orchestration_trace = trace.get("trace", {}).get("orchestrationTrace", {})
    start_ns = _extract_start_ns(trace, root_span)
    model_invocation_input = orchestration_trace.get("modelInvocationInput", {})
    if model_invocation_input:
        return _model_invocation_input_span(model_invocation_input, parent, start_ns, root_span), False
    model_invocation_output = orchestration_trace.get("modelInvocationOutput", {})
    if model_invocation_output:
        return _model_invocation_output_span(model_invocation_output, current_active_span, root_span), True
    rationale = orchestration_trace.get("rationale", {})
    if rationale:
        return _rationale_span(rationale, parent, start_ns, root_span), True
    invocation_input = orchestration_trace.get("invocationInput", {})
    if invocation_input:
        return _invocation_input_span(invocation_input, parent, start_ns, root_span), False
    observation = orchestration_trace.get("observation", {})
    if observation and isinstance(observation, dict):
        return _observation_span(observation, root_span, current_active_span), True
    return None, False


def _translate_failure_trace(
    trace: dict[str, Any], root_span: Span, parent: Span, current_active_span: Optional[Span], trace_step_id: str
) -> tuple[Optional[Span], bool]:
    failure_trace = trace.get("trace", {}).get("failureTrace", {})
    failure_metadata = failure_trace.get("metadata", {})
    start_ns, duration_ns = _extract_start_and_duration_from_metadata(failure_metadata, root_span)
    try:
        raise BedrockFailureException(failure_trace.get("failureReason", ""))
    except BedrockFailureException:
        root_span.set_exc_info(*sys.exc_info())
    error_msg = failure_trace.get("failureReason", "")
    error_type = failure_trace.get("failureType", "")
    span = _build_step_span(
        span_name="failureEvent",
        root_span=root_span,
        parent=parent,
        span_kind="task",
        start_ns=start_ns,
    )
    # Set error tags directly (instead of set_exc_info) so the LLMObs payload exposes the AWS
    # failureType/failureReason verbatim and we don't surface a synthetic exception in APM.
    span.error = 1
    span.set_tag(ERROR_MSG, error_msg)
    span.set_tag(ERROR_TYPE, error_type)
    span.finish(finish_time=(start_ns + duration_ns) / 1e9)
    return span, True


def _translate_guardrail_trace(
    trace: dict[str, Any], root_span: Span, parent: Span, current_active_span: Optional[Span], trace_step_id: str
) -> tuple[Optional[Span], bool]:
    guardrail_trace = trace.get("trace", {}).get("guardrailTrace", {})
    guardrail_metadata = guardrail_trace.get("metadata", {})
    start_ns, duration_ns = _extract_start_and_duration_from_metadata(guardrail_metadata, root_span)
    action = guardrail_trace.get("action", "")
    guardrail_output = {
        "action": action,
        "inputAssessments": guardrail_trace.get("inputAssessments", []),
        "outputAssessments": guardrail_trace.get("outputAssessments", []),
    }
    guardrail_triggered = bool(action == "INTERVENED")
    span = _build_step_span(
        span_name="guardrail",
        root_span=root_span,
        parent=parent,
        span_kind="task",
        start_ns=start_ns,
        output_val=safe_json(guardrail_output),
    )
    if guardrail_triggered:
        try:
            raise BedrockGuardrailTriggeredException("Guardrail intervened")
        except BedrockGuardrailTriggeredException:
            root_span.set_exc_info(*sys.exc_info())
        span.error = 1
        span.set_tag(ERROR_MSG, "Guardrail intervened")
        span.set_tag(ERROR_TYPE, "GuardrailTriggered")
    span.finish(finish_time=(start_ns + duration_ns) / 1e9)
    return span, True


def _translate_post_processing_trace(
    trace: dict[str, Any], root_span: Span, parent: Span, current_active_span: Optional[Span], trace_step_id: str
) -> tuple[Optional[Span], bool]:
    postprocessing_trace = trace.get("trace", {}).get("postProcessingTrace", {})
    start_ns = _extract_start_ns(trace, root_span)
    model_invocation_input = postprocessing_trace.get("modelInvocationInput", {})
    if model_invocation_input:
        return _model_invocation_input_span(model_invocation_input, parent, start_ns, root_span), False
    model_invocation_output = postprocessing_trace.get("modelInvocationOutput", {})
    if model_invocation_output:
        return _model_invocation_output_span(model_invocation_output, current_active_span, root_span), True
    return None, False


def _translate_pre_processing_trace(
    trace: dict[str, Any], root_span: Span, parent: Span, current_active_span: Optional[Span], trace_step_id: str
) -> tuple[Optional[Span], bool]:
    preprocessing_trace = trace.get("trace", {}).get("preProcessingTrace", {})
    start_ns = _extract_start_ns(trace, root_span)
    model_invocation_input = preprocessing_trace.get("modelInvocationInput", {})
    if model_invocation_input:
        return _model_invocation_input_span(model_invocation_input, parent, start_ns, root_span), False
    model_invocation_output = preprocessing_trace.get("modelInvocationOutput", {})
    if model_invocation_output:
        return _model_invocation_output_span(model_invocation_output, current_active_span, root_span), True
    return None, False


def _translate_routing_classifier_trace(
    trace: dict[str, Any], root_span: Span, parent: Span, current_active_span: Optional[Span], trace_step_id: str
) -> tuple[Optional[Span], bool]:
    routing_trace = trace.get("trace", {}).get("routingClassifierTrace", {})
    start_ns = _extract_start_ns(trace, root_span)
    model_invocation_input = routing_trace.get("modelInvocationInput", {})
    if model_invocation_input:
        return _model_invocation_input_span(model_invocation_input, parent, start_ns, root_span), False
    model_invocation_output = routing_trace.get("modelInvocationOutput", {})
    if model_invocation_output:
        return _model_invocation_output_span(model_invocation_output, current_active_span, root_span), True
    invocation_input = routing_trace.get("invocationInput", {})
    if invocation_input:
        return _invocation_input_span(invocation_input, parent, start_ns, root_span), False
    observation = routing_trace.get("observation", {})
    if observation and isinstance(observation, dict):
        return _observation_span(observation, root_span, current_active_span), True
    return None, False


def _model_invocation_input_span(
    model_input: dict[str, Any], parent: Span, start_ns: int, root_span: Span
) -> Optional[Span]:
    model_id = model_input.get("foundationModel", "")
    model_provider, model_name = parse_model_id(model_id)
    try:
        text = json.loads(model_input.get("text", "{}"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        log.warning("Failed to decode model input text.")
        text = {}
    input_messages: list[Message] = [Message(content=text.get("system", ""), role="system")]
    for message in text.get("messages", []):
        input_messages.append(Message(content=message.get("content", ""), role=message.get("role", "")))
    return _build_step_span(
        "modelInvocation",
        root_span,
        parent,
        "llm",
        start_ns=start_ns,
        metadata={"model_name": model_name, "model_provider": model_provider},
        input_val=input_messages,
    )


def _model_invocation_output_span(
    model_output: dict[str, Any], current_active_span: Optional[Span], root_span: Span
) -> Optional[Span]:
    """Finalize the pending model-invocation span with output, metrics, and back-dated finish."""
    if not current_active_span:
        log.warning("Error in processing modelInvocationOutput.")
        return None
    bedrock_metadata = model_output.get("metadata", {})
    start_ns, duration_ns = _extract_start_and_duration_from_metadata(bedrock_metadata, root_span)
    output_messages: list[Message] = []
    parsed_response = model_output.get("parsedResponse", {})
    if parsed_response:
        output_messages.append(Message(content=safe_json(parsed_response) or "", role="assistant"))
    else:
        raw_response = model_output.get("rawResponse", {}).get("content", "")
        output_messages.append(Message(content=raw_response, role="assistant"))

    reasoning_text = model_output.get("reasoningContent", {}).get("reasoningText", {})
    metadata_update = {"reasoningText": str(reasoning_text.get("text", ""))} if reasoning_text else None
    token_metrics = {
        "input_tokens": bedrock_metadata.get("usage", {}).get("inputTokens", 0),
        "output_tokens": bedrock_metadata.get("usage", {}).get("outputTokens", 0),
    }
    current_active_span.start_ns = int(start_ns)
    _annotate_llmobs_span_data(
        current_active_span, output_messages=output_messages, metrics=token_metrics, metadata=metadata_update
    )
    current_active_span.finish(finish_time=(start_ns + duration_ns) / 1e9)
    return current_active_span


def _rationale_span(rationale: dict[str, Any], parent: Span, start_ns: int, root_span: Span) -> Optional[Span]:
    span = _build_step_span(
        "reasoning", root_span, parent, "task", start_ns=start_ns, output_val=rationale.get("text", "")
    )
    span.finish(finish_time=(start_ns + DEFAULT_SPAN_DURATION_NS) / 1e9)
    return span


def _invocation_input_span(
    invocation_input: dict[str, Any], parent: Span, start_ns: int, root_span: Span
) -> Optional[Span]:
    span_name = ""
    tool_metadata = {}
    tool_args = {}
    invocation_type = invocation_input.get("invocationType", "")
    if invocation_type == "ACTION_GROUP":
        bedrock_tool_call = invocation_input.get("actionGroupInvocationInput", {})
        span_name = bedrock_tool_call.get("actionGroupName")
        params = bedrock_tool_call.get("parameters", {})
        tool_args = {arg["name"]: str(arg["value"]) for arg in params}
        tool_metadata = {
            "function": bedrock_tool_call.get("function", ""),
            "execution_type": bedrock_tool_call.get("executionType", ""),
        }
    elif invocation_type == "AGENT_COLLABORATOR":
        bedrock_tool_call = invocation_input.get("agentCollaboratorInvocationInput", {})
        span_name = bedrock_tool_call.get("agentCollaboratorName")
        tool_args = {"text": str(bedrock_tool_call.get("input", {}).get("text", ""))}
    elif invocation_type == "ACTION_GROUP_CODE_INTERPRETER":
        bedrock_tool_call = invocation_input.get("codeInterpreterInvocationInput", {})
        span_name = bedrock_tool_call.get("actionGroupName")
        tool_args = {"code": str(bedrock_tool_call.get("code", "")), "files": str(bedrock_tool_call.get("files", ""))}
    elif invocation_type == "KNOWLEDGE_BASE":
        bedrock_tool_call = invocation_input.get("knowledgeBaseLookupInput", {})
        span_name = bedrock_tool_call.get("knowledgeBaseId")
        tool_args = {"text": str(bedrock_tool_call.get("text", ""))}
    return _build_step_span(
        span_name or "", root_span, parent, "tool", start_ns, metadata=tool_metadata, input_val=safe_json(tool_args)
    )


def _observation_span(
    observation: dict[str, Any], root_span: Span, current_active_span: Optional[Span]
) -> Optional[Span]:
    """Finalize the pending tool span with output and back-dated finish."""
    observation_type = observation.get("type", "")
    if observation_type in ("FINISH", "REPROMPT"):
        # There shouldn't be a corresponding active span for a finish/reprompt observation.
        return None
    if not current_active_span:
        log.warning("Error in processing observation.")
        return None
    output_value = ""
    bedrock_metadata = {}
    if observation_type == "ACTION_GROUP":
        output_chunk = observation.get("actionGroupInvocationOutput", {})
        bedrock_metadata = output_chunk.get("metadata", {})
        output_value = output_chunk.get("text", "")
    elif observation_type == "AGENT_COLLABORATOR":
        output_chunk = observation.get("agentCollaboratorInvocationOutput", {})
        bedrock_metadata = output_chunk.get("metadata", {})
        output_value = output_chunk.get("output", {}).get("text", "")
    elif observation_type == "KNOWLEDGE_BASE":
        output_chunk = observation.get("knowledgeBaseLookupOutput", {})
        bedrock_metadata = output_chunk.get("metadata", {})
        output_value = output_chunk.get("retrievedReferences", {}).get("text", "")
    elif observation_type == "ACTION_GROUP_CODE_INTERPRETER":
        output_chunk = observation.get("codeInterpreterInvocationOutput", {})
        bedrock_metadata = output_chunk.get("metadata", {})
        output_value = output_chunk.get("executionOutput", "")

    start_ns, duration_ns = _extract_start_and_duration_from_metadata(bedrock_metadata, root_span)
    current_active_span.start_ns = int(start_ns)
    _annotate_llmobs_span_data(current_active_span, output_value=output_value)
    current_active_span.finish(finish_time=(start_ns + duration_ns) / 1e9)
    return current_active_span


# Maps Bedrock trace object names to their corresponding translation methods.
BEDROCK_AGENTS_TRACE_CONVERSION_METHODS = {
    "customOrchestrationTrace": _translate_custom_orchestration_trace,
    "failureTrace": _translate_failure_trace,
    "guardrailTrace": _translate_guardrail_trace,
    "orchestrationTrace": _translate_orchestration_trace,
    "postProcessingTrace": _translate_post_processing_trace,
    "preProcessingTrace": _translate_pre_processing_trace,
    "routingClassifierTrace": _translate_routing_classifier_trace,
}


def translate_bedrock_trace(
    trace: dict[str, Any],
    root_span: Span,
    parent: Span,
    current_active_span: Optional[Span],
    trace_step_id: str,
) -> tuple[Optional[Span], bool]:
    """Route a single bedrock trace event to the appropriate translation method.

    Returns the inner Span (created or finalized) plus a ``finished`` flag. ``finished=True`` means
    the span has already been finished; ``finished=False`` means it is pending and the caller must
    keep it as the active span for ``trace_step_id`` until the matching output/observation arrives.
    """
    trace_type = _extract_trace_type(trace) or ""
    if trace_type not in BEDROCK_AGENTS_TRACE_CONVERSION_METHODS:
        log.warning("Unsupported trace type '%s' in Bedrock trace: %s", trace_type, trace)
        return None, False
    nested_trace_dict = trace.get("trace", {}).get(trace_type, {})
    if not nested_trace_dict or not isinstance(nested_trace_dict, dict):
        log.warning("Invalid trace structure for trace type '%s': %s", trace_type, trace)
        return None, False
    if trace_type not in ("customOrchestrationTrace", "failureTrace", "guardrailTrace") and len(nested_trace_dict) != 1:
        log.warning("Invalid trace structure for trace type '%s': %s", trace_type, trace)
        return None, False
    translation_method = BEDROCK_AGENTS_TRACE_CONVERSION_METHODS[trace_type]
    return translation_method(trace, root_span, parent, current_active_span, trace_step_id)
