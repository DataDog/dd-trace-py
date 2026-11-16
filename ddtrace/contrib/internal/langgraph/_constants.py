COMPONENT = "langgraph"
# Span context item marking a node span that traces RunnableSeq.astream, whose final output is only
# returned by _consume_aiter.
SPAN_TRACES_ASTREAM = "langgraph.span_traces_astream"
# Dispatched with (span, output) after _consume_aiter returns inside a node span marked with
# SPAN_TRACES_ASTREAM, so LLMObs can record the node's aggregated output.
CONSUME_AITER_EVENT = "langgraph.consume_aiter"
# Dispatched with (agent, args, kwargs) after prebuilt.create_react_agent, so LLMObs can record the
# agent's manifest.
CREATE_REACT_AGENT_EVENT = "langgraph.create_react_agent"
# Dispatched with (loop, finished_tasks, more_tasks) after PregelLoop.tick, so LLMObs can link the tasks the
# tick queued (now loop.tasks) to the finished tasks that triggered them.
PREGEL_LOOP_TICK_EVENT = "langgraph.pregel_loop.tick"
