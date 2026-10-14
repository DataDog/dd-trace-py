"""Legacy re-export shim for a moved module.

``ddtrace.testing.internal.telemetry`` moved to:

``ddtrace_testing.internal.telemetry``

Keep the historical import path working for external consumers; import from
the new module instead.
"""

from ddtrace_testing.internal.telemetry import CIVISIBILITY as CIVISIBILITY  # noqa: F401
from ddtrace_testing.internal.telemetry import ErrorType as ErrorType  # noqa: F401
from ddtrace_testing.internal.telemetry import EventType as EventType  # noqa: F401
from ddtrace_testing.internal.telemetry import GitTelemetry as GitTelemetry  # noqa: F401
from ddtrace_testing.internal.telemetry import PayloadFileTelemetryAPI as PayloadFileTelemetryAPI  # noqa: F401
from ddtrace_testing.internal.telemetry import TelemetryAPI as TelemetryAPI  # noqa: F401
from ddtrace_testing.internal.telemetry import TelemetryAPIRequestMetrics as TelemetryAPIRequestMetrics  # noqa: F401
from ddtrace_testing.internal.telemetry import log as log  # noqa: F401
