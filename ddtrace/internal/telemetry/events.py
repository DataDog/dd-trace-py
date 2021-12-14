from typing import Dict
from typing import List
from typing import Union

from ddtrace.internal.compat import TypedDict

from .data import Dependency
from .data import Integration
from .data import create_dependency


AppStartedEvent = TypedDict(
    "AppStartedEvent",
    {
        "dependencies": List[Dependency],
        "configurations": Dict[str, str],
    },
)

AppIntegrationsChangedEvent = TypedDict(
    "AppIntegrationsChangedEvent",
    {
        "integrations": List[Integration],
    },
)

AppClosedEvent = TypedDict("AppClosedEvent", {})

Event = Union[AppStartedEvent, AppIntegrationsChangedEvent, AppClosedEvent]


def get_app_dependencies():
    # type: () -> List[Dependency]
    """returns a list of all package names and version in the working set of an applications"""
    import pkg_resources

    return [create_dependency(pkg.project_name, pkg.version) for pkg in pkg_resources.working_set]


def get_app_configurations():
    # type: () -> Dict[str, str]
    """returns a map of all configured datadog enviornment vairables"""
    return {}
