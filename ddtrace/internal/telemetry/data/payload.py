from abc import ABC
from abc import abstractmethod
from typing import Dict
from typing import List
from typing import Literal

from .dependency import Dependency
from .dependency import create_dependency
from .integration import Integration


RequestType = Literal[
    "app-closed",
    "app-started",
    "app-dendencies-load",
    "app-integrations-changed",
    "app-heartbeat",
    "app-generate-metrics",
]


class Payload(ABC):
    """Meta class which ensures all telemetry payloads implement the abstract methods listed below."""

    @abstractmethod
    def request_type(self):
        # type: () -> RequestType
        """
        every payload must return one of the following request types:
        app-closed, app-started, app-dendencies-load, app-integrations-changed, app-heartbeat, app-generate-metrics
        """
        raise NotImplementedError

    @abstractmethod
    def to_dict(self):
        # type: () -> Dict
        """
        the return value of this method is used to convert a Payload object into a json.
        All payload fields that are required by the telemetry intake service must be set here
        """
        raise NotImplementedError


class AppIntegrationsChangedPayload(Payload):
    """Payload of a TelemetryRequest which is sent after we attempt to instrument a module"""

    def __init__(self, integrations):
        # type: (List[Integration]) -> None
        super().__init__()
        self.integrations = integrations  # type: List[Integration]

    def request_type(self):
        return "app-integrations-changed"

    def to_dict(self):
        return {"integrations": self.integrations}


class AppStartedPayload(Payload):
    """Payload of a TelemetryRequest which is sent at the start of the an application"""

    def __init__(self):
        # type: () -> None
        super().__init__()
        self.dependencies = self.get_dependencies()

    def request_type(self):
        return "app-started"

    def to_dict(self):
        return {
            "dependencies": self.dependencies,
        }

    def get_dependencies(self):
        # type: () -> List[Dependency]
        """returns a list of all package names and version in the working set of an applications"""
        import pkg_resources

        return [create_dependency(pkg.project_name, pkg.version) for pkg in pkg_resources.working_set]


class AppClosedPayload(Payload):
    """
    A payload with the request_type app-closed notifies the intake service
    that an application instance has terminated
    """

    def request_type(self):
        return "app-closed"

    def to_dict(self):
        return {}
