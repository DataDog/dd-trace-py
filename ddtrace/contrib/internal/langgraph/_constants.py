COMPONENT = "langgraph"
# Span context item marking a node span that traces RunnableSeq.astream, whose final output is only
# returned by _consume_aiter.
SPAN_TRACES_ASTREAM = "langgraph.span_traces_astream"
# Span context item holding the aggregated output that _consume_aiter returned inside a node span marked
# with SPAN_TRACES_ASTREAM. Stored on the span rather than dispatched, so LLMObs reads it when the span
# finishes even if the integration is unpatched while the stream is in flight.
ASTREAM_OUTPUT = "langgraph.astream.output"
# Dispatched with (agent, args, kwargs) after prebuilt.create_react_agent, so LLMObs can record the
# agent's manifest.
CREATE_REACT_AGENT_EVENT = "langgraph.create_react_agent"
# Dispatched with (loop, finished_tasks, more_tasks) after PregelLoop.tick, so LLMObs can link the tasks the
# tick queued (now loop.tasks) to the finished tasks that triggered them.
PREGEL_LOOP_TICK_EVENT = "langgraph.pregel_loop.tick"
