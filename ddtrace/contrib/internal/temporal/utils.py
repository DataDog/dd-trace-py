from typing import Any
from enum import Enum

class TemporalSpanAttributesKeys(str, Enum):
    ACTIVITY_ID = "ActivityID"
    ACTIVITY_TYPE = "ActivityType"
    ATTEMPT = "Attempt"
    CHILD_WORKFLOW_ID = "ChildWorkflowID"
    CHILD_WORKFLOW_TYPE = "ChildWorkflowType"
    EXTERNAL_WORKFLOW_ID = "ExternalWorkflowID"
    LOCAL = "Local"
    NAMESPACE = "Namespace"
    NEXUS_OPERATION = "NexusOperation"
    NEXUS_SERVICE = "NexusService"
    QUERY_TYPE = "QueryType"
    RUN_ID = "RunID"
    SIGNAL_NAME = "SignalName"
    UPDATE_ID = "UpdateID"
    UPDATE_NAME = "UpdateName"
    WORKFLOW_ID = "WorkflowID"
    WORKFLOW_TYPE = "WorkflowType"


COMMON_ATTRIBUTE_MAP: tuple[tuple[str, str], ...] = (
    ("signal", TemporalSpanAttributesKeys.SIGNAL_NAME),
    ("query", TemporalSpanAttributesKeys.QUERY_TYPE),
    ("activity", TemporalSpanAttributesKeys.ACTIVITY_TYPE),
    ("child_workflow_id", TemporalSpanAttributesKeys.CHILD_WORKFLOW_ID),
    ("workflow_id", TemporalSpanAttributesKeys.EXTERNAL_WORKFLOW_ID),
    ("service", TemporalSpanAttributesKeys.NEXUS_SERVICE),
    ("operation_name", TemporalSpanAttributesKeys.NEXUS_OPERATION),
)

def get_common_attributes(input: Any):
    attributes: dict[str, Any] = {TemporalSpanAttributesKeys.WORKFLOW_ID: input.id}
    for field, span_key in COMMON_ATTRIBUTE_MAP:
        if val := getattr(input, field, None):
            attributes[span_key] = val

    return attributes

def get_workflow_attributes(input: Any):
    attributes: dict[str, Any] = get_common_attributes(input)
    if getattr(input, "workflow", None):
        attributes[TemporalSpanAttributesKeys.WORKFLOW_TYPE] = input.workflow
    if getattr(input, "update", None):
        attributes[TemporalSpanAttributesKeys.UPDATE_NAME] = input.update
    if getattr(input, "update_id", None):
        attributes[TemporalSpanAttributesKeys.UPDATE_ID] = input.update_id
    return attributes

def get_activity_attributes(input: Any) -> dict[str, Any]:
    return {
        TemporalSpanAttributesKeys.ACTIVITY_ID: input.id,
        TemporalSpanAttributesKeys.ACTIVITY_TYPE: input.activity_type,
    }