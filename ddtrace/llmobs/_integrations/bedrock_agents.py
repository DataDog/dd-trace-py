from datetime import timezone
import json
import sys
from typing import Any
from typing import Dict
from typing import Optional
from typing import Tuple

from ddtrace._trace.span import Span
from ddtrace.constants import ERROR_MSG
from ddtrace.constants import ERROR_TYPE
from ddtrace.internal._rand import rand128bits
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.formats import format_trace_id
from ddtrace.llmobs._constants import LLMOBS_TRACE_ID
from ddtrace.llmobs._integrations.bedrock_utils import parse_model_id
from ddtrace.llmobs._utils import _get_ml_app
from ddtrace.llmobs._utils import _get_session_id
from ddtrace.llmobs._utils import safe_json


log = get_logger(__name__)


DEFAULT_SPAN_DURATION = 1e6  # Default span duration if not provided by bedrock trace event


class BedrockGuardrailTriggeredException(Exception):
    """Custom exception to represent Bedrock Agent Guardrail Triggered trace events."""

    pass


class BedrockFailureException(Exception):
    """Custom exception to represent Bedrock Agent Failure trace events."""

    pass


def _build_span_event(
    span_name,
    root_span,
    parent_id,
    span_kind,
    start_ns=None,
    duration_ns=None,
    error=None,
    error_msg=None,
    error_type=None,
    span_id=None,
    metadata=None,
    input_val=None,
    output_val=None,
):
    if span_id is None:
        span_id = rand128bits()
    apm_trace_id = format_trace_id(root_span.trace_id)
    llmobs_trace_id = root_span._get_ctx_item(LLMOBS_TRACE_ID)
    if llmobs_trace_id is None:
        llmobs_trace_id = root_span.trace_id
    session_id = _get_session_id(root_span)
    ml_app = _get_ml_app(root_span)
    tags = [f"ml_app:{ml_app}", f"session_id:{session_id}", "integration:bedrock_agents"]
    span_event = {
        "name": span_name,
        "span_id": str(span_id),
        "trace_id": format_trace_id(llmobs_trace_id),
        "parent_id": str(parent_id or root_span.span_id),
        "tags": tags,
        "start_ns": int(start_ns or root_span.start_ns),
        "duration": int(duration_ns or DEFAULT_SPAN_DURATION),
        "status": "error" if error else "ok",
        "meta": {
            "span.kind": str(span_kind),
            "metadata": {},
            "input": {},
            "output": {},
        },
        "metrics": {},
        "_dd": {
            "span_id": str(span_id),
            "trace_id": format_trace_id(llmobs_trace_id),
            "apm_trace_id": apm_trace_id,
        },
    }
    if metadata is not None:
        span_event["meta"]["metadata"] = metadata
    io_key = "messages" if span_kind == "llm" else "value"
    if input_val is not None:
        span_event["meta"]["input"][io_key] = input_val
    if output_val is not None:
        span_event["meta"]["output"][io_key] = output_val
    if error_msg is not None:
        span_event["meta"][ERROR_MSG] = error_msg
    if error_type is not None:
        span_event["meta"][ERROR_TYPE] = error_type
    return span_event


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


def _create_or_update_bedrock_trace_step_span(trace, trace_step_id, inner_span_event, root_span, span_dict):
    """Creates/updates a Bedrock trace step span based on the provided trace and inner span event.
    Sets the trace step span's input from the first inner span event, and the output from the last inner span event.
    """
    trace_type = _extract_trace_type(trace) or "Bedrock Agent"
    span_event = span_dict.get(trace_step_id)
    if not span_event:
        start_ns = root_span.start_ns if not inner_span_event else inner_span_event.get("start_ns", root_span.start_ns)
        span_event = _build_span_event(
            span_name="{} Step".format(trace_type),
            root_span=root_span,
            parent_id=root_span.span_id,
            span_kind="workflow",
            start_ns=start_ns,
            span_id=trace_step_id,
            metadata={"bedrock_trace_id": trace_step_id},
        )
        span_dict[trace_step_id] = span_event
    trace_step_input = span_event.get("meta", {}).get("input", {})
    if not trace_step_input and inner_span_event and inner_span_event.get("meta", {}).get("input"):
        span_event["meta"]["input"] = inner_span_event.get("meta", {}).get("input")
    if inner_span_event and inner_span_event.get("meta", {}).get("output"):
        span_event["meta"]["output"] = inner_span_event.get("meta", {}).get("output")
    if not inner_span_event or not inner_span_event.get("start_ns") or not inner_span_event.get("duration"):
        return span_event
    span_event["duration"] = int(inner_span_event["duration"] + inner_span_event["start_ns"] - span_event["start_ns"])
    return span_event


def _translate_custom_orchestration_trace(
    trace: Dict[str, Any], root_span: Span, current_active_span: Optional[Dict[str, Any]], trace_step_id: str
) -> Tuple[Optional[Dict[str, Any]], bool]:
    """Translates a custom orchestration bedrock trace into a LLMObs span event.
    Returns the translated span event and a boolean indicating if the trace is finished.
    """
    custom_orchestration_trace = trace.get("trace", {}).get("customOrchestrationTrace", {})
    start_ns = _extract_start_ns(trace, root_span)
    custom_orchestration_event = custom_orchestration_trace.get("event", {})
    if not custom_orchestration_event or not isinstance(custom_orchestration_event, dict):
        return None, False
    span_event = _build_span_event(
        span_name="customOrchestration",
        root_span=root_span,
        parent_id=trace_step_id,
        span_kind="task",
        start_ns=start_ns,
        output_val=custom_orchestration_event.get("text", ""),
    )
    return span_event, False


def _translate_orchestration_trace(
    trace: Dict[str, Any], root_span: Span, current_active_span: Optional[Dict[str, Any]], trace_step_id: str
) -> Tuple[Optional[Dict[str, Any]], bool]:
    """Translates an orchestration bedrock trace into a LLMObs span event.
    Returns the translated span event and a boolean indicating if the trace is finished.
    """
    orchestration_trace = trace.get("trace", {}).get("orchestrationTrace", {})
    start_ns = _extract_start_ns(trace, root_span)
    model_invocation_input = orchestration_trace.get("modelInvocationInput", {})
    if model_invocation_input:
        return _model_invocation_input_span(model_invocation_input, trace_step_id, start_ns, root_span), False
    model_invocation_output = orchestration_trace.get("modelInvocationOutput", {})
    if model_invocation_output:
        return _model_invocation_output_span(model_invocation_output, current_active_span, root_span), True
    rationale = orchestration_trace.get("rationale", {})
    if rationale:
        return _rationale_span(rationale, trace_step_id, start_ns, root_span), True
    invocation_input = orchestration_trace.get("invocationInput", {})
    if invocation_input:
        return _invocation_input_span(invocation_input, trace_step_id, start_ns, root_span), False
    observation = orchestration_trace.get("observation", {})
    if observation:
        return _observation_span(observation, root_span, current_active_span), True
    return None, False


def _translate_failure_trace(
    trace: Dict[str, Any], root_span: Span, current_active_span: Optional[Dict[str, Any]], trace_step_id: str
) -> Tuple[Optional[Dict[str, Any]], bool]:
    """Translates a failure bedrock trace into a LLMObs span event.
    Returns the translated span event and a boolean indicating that the span is finished.
    """
    failure_trace = trace.get("trace", {}).get("failureTrace", {})
    failure_metadata = failure_trace.get("metadata", {})
    start_ns, duration_ns = _extract_start_and_duration_from_metadata(failure_metadata, root_span)
    try:
        raise BedrockFailureException(failure_trace.get("failureReason", ""))
    except BedrockFailureException:
        root_span.set_exc_info(*sys.exc_info())
    error_msg = failure_trace.get("failureReason", "")
    error_type = failure_trace.get("failureType", "")
    span_event = _build_span_event(
        span_name="failureEvent",
        root_span=root_span,
        parent_id=trace_step_id,
        span_kind="task",
        start_ns=start_ns,
        duration_ns=duration_ns,
        error=True,
        error_msg=error_msg,
        error_type=error_type,
    )
    return span_event, True


def _translate_guardrail_trace(
    trace: Dict[str, Any], root_span: Span, current_active_span: Optional[Dict[str, Any]], trace_step_id: str
) -> Tuple[Optional[Dict[str, Any]], bool]:
    """Translates a guardrail bedrock trace into a LLMObs span event.
    Returns the translated span event and a boolean indicating that the span is finished.
    """
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
    span_event = _build_span_event(
        span_name="guardrail",
        root_span=root_span,
        parent_id=trace_step_id,
        span_kind="task",
        start_ns=start_ns,
        duration_ns=duration_ns,
        error=guardrail_triggered,
        error_msg="Guardrail intervened" if guardrail_triggered else None,
        error_type="GuardrailTriggered" if guardrail_triggered else None,
        output_val=safe_json(guardrail_output),
    )
    if guardrail_triggered:
        try:
            raise BedrockGuardrailTriggeredException("Guardrail intervened")
        except BedrockGuardrailTriggeredException:
            root_span.set_exc_info(*sys.exc_info())
    return span_event, True


def _translate_post_processing_trace(
    trace: Dict[str, Any], root_span: Span, current_active_span: Optional[Dict[str, Any]], trace_step_id: str
) -> Tuple[Optional[Dict[str, Any]], bool]:
    """Translates a postprocessing bedrock trace into a LLMObs span event.
    Returns the translated span event and a boolean indicating if the span is finished.
    """
    postprocessing_trace = trace.get("trace", {}).get("postProcessingTrace", {})
    start_ns = _extract_start_ns(trace, root_span)
    model_invocation_input = postprocessing_trace.get("modelInvocationInput", {})
    if model_invocation_input:
        return _model_invocation_input_span(model_invocation_input, trace_step_id, start_ns, root_span), False
    model_invocation_output = postprocessing_trace.get("modelInvocationOutput", {})
    if model_invocation_output:
        return _model_invocation_output_span(model_invocation_output, current_active_span, root_span), True
    return None, False


def _translate_pre_processing_trace(
    trace: Dict[str, Any], root_span: Span, current_active_span: Optional[Dict[str, Any]], trace_step_id: str
) -> Tuple[Optional[Dict[str, Any]], bool]:
    """Translates a preprocessing bedrock trace into a LLMObs span event.
    Returns the translated span event and a boolean indicating if the span is finished.
    """
    preprocessing_trace = trace.get("trace", {}).get("preProcessingTrace", {})
    start_ns = _extract_start_ns(trace, root_span)
    model_invocation_input = preprocessing_trace.get("modelInvocationInput", {})
    if model_invocation_input:
        return _model_invocation_input_span(model_invocation_input, trace_step_id, start_ns, root_span), False
    model_invocation_output = preprocessing_trace.get("modelInvocationOutput", {})
    if model_invocation_output:
        return _model_invocation_output_span(model_invocation_output, current_active_span, root_span), True
    return None, False


def _translate_routing_classifier_trace(
    trace: Dict[str, Any], root_span: Span, current_active_span: Optional[Dict[str, Any]], trace_step_id: str
) -> Tuple[Optional[Dict[str, Any]], bool]:
    """Translates a routing classifier bedrock trace into a LLMObs span event.
    Returns the translated span event and a boolean indicating if the span is finished.
    """
    routing_trace = trace.get("trace", {}).get("routingClassifierTrace", {})
    start_ns = _extract_start_ns(trace, root_span)
    model_invocation_input = routing_trace.get("modelInvocationInput", {})
    if model_invocation_input:
        return _model_invocation_input_span(model_invocation_input, trace_step_id, start_ns, root_span), False
    model_invocation_output = routing_trace.get("modelInvocationOutput", {})
    if model_invocation_output:
        return _model_invocation_output_span(model_invocation_output, current_active_span, root_span), True
    invocation_input = routing_trace.get("invocationInput", {})
    if invocation_input:
        return _invocation_input_span(invocation_input, trace_step_id, start_ns, root_span), False
    observation = routing_trace.get("observation", {})
    if observation:
        return _observation_span(observation, root_span, current_active_span), True
    return None, False


def _model_invocation_input_span(
    model_input: Dict[str, Any], trace_step_id: str, start_ns: int, root_span: Span
) -> Optional[Dict[str, Any]]:
    """Translates a Bedrock model invocation input trace into a LLMObs span event."""
    model_id = model_input.get("foundationModel", "")
    model_provider, model_name = parse_model_id(model_id)
    try:
        text = json.loads(model_input.get("text", "{}"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        log.warning("Failed to decode model input text.")
        text = {}
    input_messages = [{"content": text.get("system", ""), "role": "system"}]
    for message in text.get("messages", []):
        input_messages.append({"content": message.get("content", ""), "role": message.get("role", "")})
    span_event = _build_span_event(
        "modelInvocation",
        root_span,
        trace_step_id,
        "llm",
        start_ns=start_ns,
        metadata={"model_name": model_name, "model_provider": model_provider},
        input_val=input_messages,
    )
    return span_event


def _model_invocation_output_span(
    model_output: Dict[str, Any], current_active_span: Optional[Dict[str, Any]], root_span: Span
) -> Optional[Dict[str, Any]]:
    """Translates a Bedrock model invocation output trace into a LLMObs span event."""
    if not current_active_span:
        log.warning("Error in processing modelInvocationOutput.")
        return None
    bedrock_metadata = model_output.get("metadata", {})
    start_ns, duration_ns = _extract_start_and_duration_from_metadata(bedrock_metadata, root_span)
    output_messages = []
    parsed_response = model_output.get("parsedResponse", {})
    if parsed_response:
        output_messages.append({"content": safe_json(parsed_response), "role": "assistant"})
    else:
        raw_response = model_output.get("rawResponse", {}).get("content", "")
        output_messages.append({"content": raw_response, "role": "assistant"})

    reasoning_text = model_output.get("reasoningContent", {}).get("reasoningText", {})
    if reasoning_text:
        current_active_span["metadata"]["reasoningText"] = str(reasoning_text.get("text", ""))
    token_metrics = {
        "input_tokens": bedrock_metadata.get("usage", {}).get("inputTokens", 0),
        "output_tokens": bedrock_metadata.get("usage", {}).get("outputTokens", 0),
    }
    current_active_span["start_ns"] = int(start_ns)
    current_active_span["duration"] = int(duration_ns)
    current_active_span["meta"]["output"]["messages"] = output_messages
    current_active_span["metrics"] = token_metrics
    return current_active_span


def _rationale_span(
    rationale: Dict[str, Any], trace_step_id: str, start_ns: int, root_span: Span
) -> Optional[Dict[str, Any]]:
    """Translates a Bedrock rationale trace into a LLMObs span event."""
    span_event = _build_span_event(
        "reasoning", root_span, trace_step_id, "task", start_ns=start_ns, output_val=rationale.get("text", "")
    )
    return span_event


def _invocation_input_span(
    invocation_input: Dict[str, Any], trace_step_id: str, start_ns: int, root_span: Span
) -> Optional[Dict[str, Any]]:
    """Translates a Bedrock invocation input trace into a LLMObs span event."""
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
    span_event = _build_span_event(
        span_name, root_span, trace_step_id, "tool", start_ns, metadata=tool_metadata, input_val=safe_json(tool_args)
    )
    return span_event


def _observation_span(
    observation: Dict[str, Any], root_span: Span, current_active_span: Optional[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """Translates a Bedrock observation trace into a LLMObs span event."""
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
    current_active_span["start_ns"] = int(start_ns)
    current_active_span["duration"] = int(duration_ns)
    current_active_span["meta"]["output"]["value"] = output_value
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


def translate_bedrock_trace(trace, root_span, current_active_span_event, trace_step_id):
    """Translates a Bedrock trace into a LLMObs span event.
    Routes the trace to the appropriate translation method based on the trace type.
    Returns the translated span event and a boolean indicating if the span is finished.
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
    translated_span_event, finished = translation_method(trace, root_span, current_active_span_event, trace_step_id)
    return translated_span_event, finished
