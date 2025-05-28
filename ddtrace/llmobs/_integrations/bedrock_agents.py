from datetime import timezone
import json
import sys

from ddtrace.internal.logger import get_logger
from ddtrace.internal._rand import rand128bits
from ddtrace.internal.utils.formats import format_trace_id
from ddtrace.constants import ERROR_TYPE
from ddtrace.constants import ERROR_MSG
from ddtrace.llmobs._utils import _get_ml_app
from ddtrace.llmobs._utils import safe_json

log = get_logger(__name__)


DEFAULT_SPAN_DURATION = 1e6  # Default span duration if not provided by bedrock trace event


def _extract_trace_step_id(bedrock_trace_obj):
    trace_part = bedrock_trace_obj.get("trace", {})
    if not trace_part or not isinstance(trace_part, dict) or len(trace_part) != 1:
        return None
    trace_type, trace_part = next(iter(trace_part.items()))
    if not trace_part or not isinstance(trace_part, dict):
        return None
    if "traceId" in trace_part or trace_type in ("customOrchestrationTrace", "failureTrace", "guardrailTrace"):
        return trace_part.get("traceId")
    if len(trace_part) != 1:
        return None
    # Other trace types have a traceID key-value pair one layer below in the nested object.
    _, trace_part = next(iter(trace_part.items()))
    return trace_part.get("traceId")


def _extract_trace_type(bedrock_trace_obj):
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
    start_ns = bedrock_metadata.get("startTime")
    if start_ns:
        start_ns = start_ns.replace(tzinfo=timezone.utc).timestamp() * 1e9
    else:
        start_ns = root_span.start_ns
    duration_ns = bedrock_metadata.get("totalTimeMs", 1) * 1e6
    return int(start_ns), int(duration_ns)


def _create_or_update_bedrock_trace_step_span(trace, inner_span_event, root_span, span_dict):
    trace_step_id = _extract_trace_step_id(trace)
    trace_type = _extract_trace_type(trace) or "Bedrock Agent"
    span_event = span_dict.get(trace_step_id)
    if not span_event:
        start_ns = root_span.start_ns if not inner_span_event else inner_span_event.get("start_ns", root_span.start_ns)
        span_event = {
            "name": "{} Step".format(trace_type),
            "span_id": str(trace_step_id),
            "trace_id": format_trace_id(root_span.trace_id),
            "parent_id": str(root_span.span_id),
            "tags": ["ml_app:{}".format(_get_ml_app(root_span))],
            "start_ns": int(start_ns),
            "duration": DEFAULT_SPAN_DURATION,
            "status": "ok",
            "meta": {"span.kind": "workflow"},
            "metrics": {},
        }
        span_dict[trace_step_id] = span_event
    if not inner_span_event or not inner_span_event.get("start_ns") or not inner_span_event.get("duration"):
        return span_event
    span_event["duration"] = int(inner_span_event["duration"] + inner_span_event["start_ns"] - span_event["start_ns"])
    return span_event


def _translate_custom_orchestration_trace(trace, root_span, current_active_span, trace_step_id):
    custom_orchestration_trace = trace.get("trace", {}).get("customOrchestrationTrace", {})
    if not custom_orchestration_trace or not isinstance(custom_orchestration_trace, dict):
        return None
    start_ns = _extract_start_ns(trace, root_span)
    custom_orchestration_event = custom_orchestration_trace.get("event", {})
    if not custom_orchestration_event or not isinstance(custom_orchestration_event, dict):
        return None
    return {
        "name": "customOrchestration",
        "span_id": str(rand128bits()),
        "trace_id": format_trace_id(root_span.trace_id),
        "parent_id": str(trace_step_id),
        "tags": ["ml_app:{}".format(_get_ml_app(root_span))],
        "start_ns": int(start_ns),
        "duration": DEFAULT_SPAN_DURATION,
        "status": "ok",
        "meta": {
            "span.kind": "tool",
            "metadata": {},
            "input": {},
            "output": {"value": custom_orchestration_event.get("text", "")},
        },
        "metrics": {},
    }



def _translate_orchestration_trace(trace, root_span, current_active_span, trace_step_id):
    orchestration_trace = trace.get("trace", {}).get("orchestrationTrace", {})
    if not orchestration_trace:
        return None
    if not isinstance(orchestration_trace, dict) or len(orchestration_trace) != 1:
        return None
    start_ns = _extract_start_ns(trace, root_span)
    model_invocation_input = orchestration_trace.get("modelInvocationInput", {})
    if model_invocation_input:
        return _model_invocation_input_span(model_invocation_input, trace_step_id, start_ns, root_span)
    model_invocation_output = orchestration_trace.get("modelInvocationOutput", {})
    if model_invocation_output:
        return _model_invocation_output_span(model_invocation_output, current_active_span, root_span)
    rationale = orchestration_trace.get("rationale", {})
    if rationale:
        return _rationale_span(rationale, trace_step_id, start_ns, root_span)
    invocation_input = orchestration_trace.get("invocationInput", {})
    if invocation_input:
        return _invocation_input_span(invocation_input, trace_step_id, start_ns, root_span)
    observation = orchestration_trace.get("observation", {})
    if observation:
        return _observation_span(observation, root_span, current_active_span)


def _translate_failure_trace(trace, root_span, current_active_span, trace_step_id):
    failure_trace = trace.get("trace", {}).get("failureTrace", {})
    if not failure_trace or not isinstance(failure_trace, dict):
        return None
    failure_metadata = failure_trace.get("metadata", {})
    start_ns, duration_ns = _extract_start_and_duration_from_metadata(failure_metadata, root_span)
    try:
        raise Exception(failure_trace.get("failureReason", ""))
    except Exception:
        root_span.set_exc_info(*sys.exc_info())
    return {
        "name": "failureEvent",
        "span_id": str(rand128bits()),
        "trace_id": format_trace_id(root_span.trace_id),
        "parent_id": str(trace_step_id),
        "tags": ["ml_app:{}".format(_get_ml_app(root_span))],
        "start_ns": int(start_ns),
        "duration": int(duration_ns),
        "status": "error",
        "meta": {
            "span.kind": "task",
            "metadata": {},
            "input": {},
            "output": {},
            ERROR_MSG: failure_trace.get("failureReason", ""),
            ERROR_TYPE: failure_trace.get("failureType", ""),

        },
        "metrics": {},
    }

def _translate_guardrail_trace(trace, root_span, current_active_span, trace_step_id):
    guardrail_trace = trace.get("trace", {}).get("guardrailTrace", {})
    if not guardrail_trace or not isinstance(guardrail_trace, dict):
        return None
    guardrail_metadata = guardrail_trace.get("metadata", {})
    start_ns, duration_ns = _extract_start_and_duration_from_metadata(guardrail_metadata, root_span)
    action = guardrail_trace.get("action", "")
    guardrail_output = {
        "action": action,
        "inputAssessments": guardrail_trace.get("inputAssessments", []),
        "outputAssessments": guardrail_trace.get("outputAssessments", []),
    }
    span_event = {
        "name": "guardrail",
        "span_id": str(rand128bits()),
        "trace_id": format_trace_id(root_span.trace_id),
        "parent_id": str(trace_step_id),
        "tags": ["ml_app:{}".format(_get_ml_app(root_span))],
        "start_ns": int(start_ns),
        "duration": int(duration_ns),
        "status": "ok",
        "meta": {
            "span.kind": "task",
            "metadata": {},
            "input": {},
            "output": {"value": safe_json(guardrail_output)},
        },
        "metrics": {},
    }
    if action == "INTERVENED":
        span_event["status"] = "error"
        span_event["meta"][ERROR_MSG] = "Guardrail triggered"
        span_event["meta"][ERROR_TYPE] = "GuardrailTriggered"
        try:
            raise Exception("Guardrail triggered")
        except Exception:
            root_span.set_exc_info(*sys.exc_info())
    return span_event


def _translate_post_processing_trace(trace, root_span, current_active_span, trace_step_id):
    postprocessing_trace = trace.get("trace", {}).get("postProcessingTrace", {})
    if not postprocessing_trace:
        return None
    if not isinstance(postprocessing_trace, dict) or len(postprocessing_trace) != 1:
        return None
    start_ns = _extract_start_ns(trace, root_span)
    model_invocation_input = postprocessing_trace.get("modelInvocationInput", {})
    if model_invocation_input:
        return _model_invocation_input_span(model_invocation_input, trace_step_id, start_ns, root_span)
    model_invocation_output = postprocessing_trace.get("modelInvocationOutput", {})
    if model_invocation_output:
        return _model_invocation_output_span(model_invocation_output, current_active_span, root_span)


def _translate_pre_processing_trace(trace, root_span, current_active_span, trace_step_id):
    preprocessing_trace = trace.get("trace", {}).get("preProcessingTrace", {})
    if not preprocessing_trace:
        return None
    if not isinstance(preprocessing_trace, dict) or len(preprocessing_trace) != 1:
        return None
    start_ns = _extract_start_ns(trace, root_span)
    model_invocation_input = preprocessing_trace.get("modelInvocationInput", {})
    if model_invocation_input:
        return _model_invocation_input_span(model_invocation_input, trace_step_id, start_ns, root_span)
    model_invocation_output = preprocessing_trace.get("modelInvocationOutput", {})
    if model_invocation_output:
        return _model_invocation_output_span(model_invocation_output, current_active_span, root_span)


def _translate_routing_classifier_trace(trace, root_span, current_active_span, trace_step_id):
    routing_trace = trace.get("trace", {}).get("routingClassifierTrace", {})
    if not routing_trace:
        return None
    if not isinstance(routing_trace, dict) or len(routing_trace) != 1:
        return None
    start_ns = _extract_start_ns(trace, root_span)
    model_invocation_input = routing_trace.get("modelInvocationInput", {})
    if model_invocation_input:
        return _model_invocation_input_span(model_invocation_input, trace_step_id, start_ns, root_span)
    model_invocation_output = routing_trace.get("modelInvocationOutput", {})
    if model_invocation_output:
        return _model_invocation_output_span(model_invocation_output, current_active_span, root_span)
    invocation_input = routing_trace.get("invocationInput", {})
    if invocation_input:
        return _invocation_input_span(invocation_input, trace_step_id, start_ns, root_span)
    observation = routing_trace.get("observation", {})
    if observation:
        return _observation_span(observation, root_span, current_active_span)


def _model_invocation_input_span(model_input, trace_step_id, start_ns, root_span):
    model = model_input.get("foundationModel", "")
    model_name = model.split(".", 1)[-1] if model else ""
    model_provider = model.split(".", 1)[0] if model else ""
    text = json.loads(model_input.get("text", "{}"))
    input_messages = [{"content": text.get("system", ""), "role": "system"}]
    for message in text.get("messages", []):
        input_messages.append({"content": message.get("content", ""), "role": message.get("role", "")})
    return {
        "name": "modelInvocation",
        "span_id": str(rand128bits()),
        "trace_id": format_trace_id(root_span.trace_id),
        "parent_id": str(trace_step_id),
        "tags": ["ml_app:{}".format(_get_ml_app(root_span))],
        "start_ns": int(start_ns),
        "duration": None,
        "status": "ok",
        "meta": {
            "span.kind": "llm",
            "metadata": {"model_name": model_name, "model_provider": model_provider},
            "input": {"messages": input_messages},
            "output": {},
        },
        "metrics": {},
    }


def _model_invocation_output_span(model_output, current_active_span, root_span):
    span_event = current_active_span
    if not span_event:
        log.warning("Error in processing modelInvocationOutput.")
        return None
    bedrock_metadata = model_output.get("metadata", {})
    start_ns, duration_ns = _extract_start_and_duration_from_metadata(bedrock_metadata, root_span)
    output_messages = [model_output.get("rawResponse", {"content": ""})]
    parsed_response = model_output.get("parsedResponse", {})
    if parsed_response:
        output_messages.append({"content": safe_json(parsed_response), "role": "assistant"})
    reasoning_text = model_output.get("reasoningContent", {}).get("reasoningText", {})
    if reasoning_text:
        span_event["metadata"]["reasoningText"] = str(reasoning_text.get("text", ""))
    token_metrics = {
        "input_tokens": bedrock_metadata.get("usage", {}).get("inputTokens", 0),
        "output_tokens": bedrock_metadata.get("usage", {}).get("outputTokens", 0),
    }
    span_event["start_ns"] = int(start_ns)
    span_event["duration"] = int(duration_ns)
    span_event["meta"]["output"]["messages"] = output_messages
    span_event["metrics"] = token_metrics
    return span_event


def _rationale_span(rationale, trace_step_id, start_ns, root_span):
    return {
        "name": "reasoning",
        "span_id": str(rand128bits()),
        "trace_id": format_trace_id(root_span.trace_id),
        "parent_id": str(trace_step_id),
        "tags": ["ml_app:{}".format(_get_ml_app(root_span))],
        "start_ns": int(start_ns),
        "duration": DEFAULT_SPAN_DURATION,
        "status": "ok",
        "meta": {
            "span.kind": "task",
            "input": {},
            "output": {"value": rationale.get("text", "")},
        },
        "metrics": {},
    }


def _invocation_input_span(invocation_input, trace_step_id, start_ns, root_span):
    span_name = ""
    tool_metadata = {}
    args = {}
    bedrock_tool_call = invocation_input.get("actionGroupInvocationInput", {})
    if bedrock_tool_call:
        span_name = bedrock_tool_call.get("actionGroupName")
        params = bedrock_tool_call.get("parameters", {})
        args = {arg["name"]: str(arg["value"]) for arg in params}
        tool_metadata = {
            "function": bedrock_tool_call.get("function", ""),
            "execution_type": bedrock_tool_call.get("executionType", "")
        }
    bedrock_tool_call = invocation_input.get("agentCollaboratorInvocationInput", {})
    if bedrock_tool_call:
        span_name = bedrock_tool_call.get("agentCollaboratorName")
        args = {"text": str(bedrock_tool_call.get("input", {}).get("text", ""))}
    bedrock_tool_call = invocation_input.get("codeInterpreterInvocationInput", {})
    if bedrock_tool_call:
        span_name = bedrock_tool_call.get("actionGroupName")
        args = {"code": str(bedrock_tool_call.get("code", "")), "files": str(bedrock_tool_call.get("files", ""))}
    bedrock_tool_call = invocation_input.get("knowledgeBaseLookupInput", {})
    if bedrock_tool_call:
        span_name = bedrock_tool_call.get("knowledgeBaseId")
        args = {"text": str(bedrock_tool_call.get("text", ""))}
    span_event = {
        "name": span_name or "invocationInput",
        "span_id": str(rand128bits()),
        "trace_id": format_trace_id(root_span.trace_id),
        "parent_id": str(trace_step_id),
        "tags": ["ml_app:{}".format(_get_ml_app(root_span))],
        "start_ns": int(start_ns),
        "duration": None,
        "status": "ok",
        "meta": {
            "span.kind": "tool",
            "metadata": tool_metadata,
            "input": {"value": safe_json(args)},
            "output": {},
        },
        "metrics": {},
    }
    return span_event

def _observation_span(observation, root_span, current_active_span):
    observation_type = observation.get("type", "")
    output_value = ""
    if observation_type == "FINISH":
        final_response = observation.get("finalResponse", {})
        root_span._set_ctx_item("_ml_obs.meta.output.value", final_response.get("text", ""))
        return None
    span_event = current_active_span
    if not span_event:
        log.warning("Error in processing observation.")
        return None
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
    elif observation_type == "REPROMPT":
        # There shouldn't be a corresponding active span for a reprompt observation.
        return None
    elif observation_type == "ACTION_GROUP_CODE_INTERPRETER":
        output_chunk = observation.get("codeInterpreterInvocationOutput", {})
        bedrock_metadata = output_chunk.get("metadata", {})
        output_value = output_chunk.get("executionOutput", "")

    start_ns, duration_ns = _extract_start_and_duration_from_metadata(bedrock_metadata, root_span)
    span_event["start_ns"] = int(start_ns)
    span_event["duration"] = int(duration_ns)
    span_event["meta"]["output"]["value"] = output_value
    return span_event


