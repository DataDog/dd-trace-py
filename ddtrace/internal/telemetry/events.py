from typing import Dict
from typing import List
from typing import Union

from ddtrace.internal.compat import TypedDict

from .data import Dependency
from .data import Integration


# Payload of a TelemetryRequest which is sent at the start of the an application
AppStartedEvent = TypedDict(
    "AppStartedEvent",
    {
        "dependencies": List[Dependency],
        "configurations": Dict[str, str],
    },
)

# Payload of a TelemetryRequest which is sent after we attempt to instrument a module
AppIntegrationsChangedEvent = TypedDict(
    "AppIntegrationsChangedEvent",
    {
        "integrations": List[Integration],
    },
)

# Payload of a TelemetryRequest which is sent after an application or process is terminated
AppClosedEvent = TypedDict("AppClosedEvent", {})

# Union type which ensures all telemetry request contain a valid payload type
Event = Union[AppStartedEvent, AppIntegrationsChangedEvent, AppClosedEvent]


def get_app_dependencies():
    # type: () -> List[Dependency]
    """returns a list of all package names and version in the working set of an applications"""
    import pkg_resources

    return [Dependency(name=pkg.project_name, version=pkg.version) for pkg in pkg_resources.working_set]


def get_app_configurations():
    # type: () -> Dict[str, str]
    """returns a map of all configured datadog enviornment vairables"""
    return {}
