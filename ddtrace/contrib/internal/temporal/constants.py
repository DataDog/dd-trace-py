"""Temporal header constants and types."""

from collections.abc import Mapping
from typing import TypeAlias
from enum import Enum

import temporalio.api.common.v1


Carrier: TypeAlias = dict[str, str]
TemporalHeader: TypeAlias = Mapping[str, temporalio.api.common.v1.Payload]

DEFAULT_HEADER_KEY = "dd_trace_span"

class TemporalOperationNames(str, Enum):
    CREATE_SCHEDULE = "CreateSchedule"
    HANDLE_QUERY = "HandleQuery"
    HANDLE_SIGNAL = "HandleSignal"
    HANDLE_UPDATE = "HandleUpdate"
    QUERY_WORKFLOW = "QueryWorkflow"
    RUN_ACTIVITY = "RunActivity"
    RUN_WORKFLOW = "RunWorkflow"
    SIGNAL_CHILD_WORKFLOW = "SignalChildWorkflow"
    SIGNAL_EXTERNAL_WORKFLOW = "SignalExternalWorkflow"
    SIGNAL_WITH_START_WORKFLOW = "SignalWithStartWorkflow"
    SIGNAL_WORKFLOW = "SignalWorkflow"
    START_ACTIVITY = "StartActivity"
    START_CHILD_WORKFLOW = "StartChildWorkflow"
    START_NEXUS_OPERATION = "StartNexusOperation"
    RUN_NEXUS_OPERATION_START_HANDLER = "RunStartNexusOperationHandler"
    RUN_NEXUS_OPERATION_CANCEL_HANDLER = "RunCancelNexusOperationHandler"
    UPDATE_WITH_START_WORKFLOW = "UpdateWithStartWorkflow"
    UPDATE_WORKFLOW = "UpdateWorkflow"
    START_WORKFLOW = "StartWorkflow"
    VALIDATE_UPDATE = "ValidateUpdate"