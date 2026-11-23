BOTOCORE_STEPFUNCTIONS_INPUT_KEY = "botocore_stepfunctions_input"

# Component of the Bedrock LlmRequestEvents, which products use to select their Bedrock handling.
BEDROCK_COMPONENT = "botocore"
# Span context item holding the trace events an InvokeAgent stream returned, set before the agent span finishes.
BEDROCK_AGENT_TRACES = "bedrock.agent.traces"
# Span context item a product may set when a ConverseStream span starts: a primed generator that receives each
# streamed chunk, and that is passed back as the request's response once the stream ends.
BEDROCK_CONVERSE_STREAM_CONSUMER = "bedrock.converse_stream.consumer"
