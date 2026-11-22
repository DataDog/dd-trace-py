COMPONENT = "crewai"
# Dispatched with (span, args, kwargs) after Crew._get_context, so LLMObs can link the queued task to its inputs.
TASK_GET_CONTEXT_EVENT = "crewai.task.get_context"
# Dispatched with (flow_span, args, kwargs, flow_instance) after Flow._find_triggered_methods, so LLMObs can link
# the triggered listener methods to their triggers.
FLOW_FIND_TRIGGERED_METHODS_EVENT = "crewai.flow.find_triggered_methods"
