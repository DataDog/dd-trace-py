"""
Ray integration for Datadog APM.

This integration traces Ray distributed computing operations including:
- Remote function execution
- Actor creation and method calls
- Object store operations (get, put, wait)
- Task scheduling and execution
- Cluster operations

The integration provides visibility into:
- Task execution time and resource usage
- Actor lifecycle and method calls
- Object store operations
- Distributed task dependencies
- Cluster resource utilization

Configuration:
    The integration can be configured using the following environment variables:
    - DD_RAY_ENABLED: Enable/disable the integration (default: true)
    - DD_RAY_SERVICE: Service name for Ray traces (default: ray)
    - DD_RAY_TRACE_TASKS: Enable task tracing (default: true)
    - DD_RAY_TRACE_ACTORS: Enable actor tracing (default: true)
    - DD_RAY_TRACE_OBJECTS: Enable object store tracing (default: true)
"""
