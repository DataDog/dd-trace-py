# makes sense to live in this file
CONTAINER_TAGS_HASH = "Datadog-Container-Tags-Hash"
DEFAULT_SAMPLING_RATE_LIMIT = 100
SAMPLING_HASH_MODULO = 1 << 64
# Big prime number to make hashing better distributed, it has to be the same factor as the Agent
# and other tracers to allow chained sampling
SAMPLING_KNUTH_FACTOR = 1111111111111111111
COMPONENT = "component"
SPAN_EVENTS_KEY = "events"
DEFAULT_BUFFER_SIZE = 20 << 20  # 20 MB
DEFAULT_MAX_PAYLOAD_SIZE = 20 << 20  # 20 MB
DEFAULT_PROCESSING_INTERVAL = 1.0
DEFAULT_REUSE_CONNECTIONS = False
DEFAULT_TIMEOUT = 2.0


# List of support values in DD_TRACE_EXPERIMENTAL_FEATURES_ENABLED
class EXPERIMENTAL_FEATURES:
    # Enables submitting runtime metrics as gauges (instead of distributions)
    RUNTIME_METRICS = "DD_RUNTIME_METRICS_ENABLED"
