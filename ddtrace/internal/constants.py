import enum


# Default service name to use when one isn't defined
UNNAMED_SERVICE_NAME = "unnamed_python_service"

# Tags propagated to downstream services
UPSTREAM_SERVICES_KEY = "_dd.p.upstream_services"


class SamplingMechanism(enum.Enum):
    """The mechanism responsible for making a sampling decision."""

    DEFAULT = 0  # Service rate sampling used before receiving rates from the agent
    AGENT_RATE = 1  # Service rate sampling used rates from the agent
    REMOTE_RATE = 2  # Service rate sampling used rates from the backend
    USER_RULE = 3  # Manual rule / rate provided in tracer configuration
    MANUAL = 4  # Manually set via `set_tag(MANUAL_DROP/KEEP_KEY)`
    APP_SEC = 5  # Sampling decision overridden by AppSec
    USER_REMOTE = 6  # User defined target rate
    DD_REMOTE = 7  # Datadog Emergency
