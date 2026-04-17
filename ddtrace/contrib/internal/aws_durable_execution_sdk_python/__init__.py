"""
AWS Durable Execution SDK instrumentation for ddtrace.

This integration provides automatic tracing for AWS Lambda Durable Functions,
including trace context propagation across suspensions and replay detection.

Enabled by default when ddtrace is initialized and the SDK is imported.
"""

from ddtrace.internal.utils.importlib import require_modules


required_modules = ["aws_durable_execution_sdk_python"]

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .patch import get_version
        from .patch import patch
        from .patch import unpatch

        __all__ = ["patch", "unpatch", "get_version"]
