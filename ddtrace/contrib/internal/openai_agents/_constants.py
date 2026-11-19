COMPONENT = "openai_agents"
# Dispatched with (span, args, kwargs) after each agent turn, so LLMObs can tag the agent manifest on the turn's span.
RUN_SINGLE_TURN_EVENT = "openai_agents.run_single_turn"
