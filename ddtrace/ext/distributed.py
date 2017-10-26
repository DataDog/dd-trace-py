# HTTP headers one should set for distributed tracing.
# These are cross-language (eg: Python, Go and other implementations should honor these)

HTTP_HEADER_TRACE_ID = 'x-datadog-trace-id'
HTTP_HEADER_PARENT_ID = 'x-datadog-parent-id'
HTTP_HEADER_SAMPLING_PRIORITY = 'x-datadog-sampling-priority'
