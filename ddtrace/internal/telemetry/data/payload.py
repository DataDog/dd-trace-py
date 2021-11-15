from abc import ABC
from abc import abstractmethod
from typing import Dict
from typing import List

from ....version import get_version
from .dependency import Dependency
from .dependency import create_dependency
from .integration import Integration
from .metrics import Series


class Payload(ABC):
    @abstractmethod
    def request_type(self):
        # type: () -> str
        pass

    @abstractmethod
    def to_dict(self):
        # type: () -> Dict
        pass


class AppIntegrationsChangedPayload(Payload):
    def __init__(self, integrations):
        # type: (List[Integration]) -> None
        super().__init__()
        self.integrations = integrations  # type: List[Integration]

    def request_type(self):
        return "app-integrations-changed"

    def to_dict(self):
        return {"integrations": self.integrations}


class AppStartedPayload(Payload):
    def __init__(self, configurations={}, additional_payload={}):
        # type: (Dict, Dict) -> None
        super().__init__()
        self.configuration = configurations  # type: Dict[str, str]
        self.additional_payload = additional_payload  # type: Dict[str, str]
        self.dependencies = self.get_dependencies()

    def request_type(self):
        return "app-started"

    def to_dict(self):
        return {
            "configuration": self.configuration,
            "additional_payload": self.additional_payload,
            "dependencies": self.dependencies,
        }

    def get_dependencies(self):
        # type: () -> List[Dependency]
        import pkg_resources

        dependencies = []
        return [create_dependency(pkg.project_name, pkg.version) for pkg in pkg_resources.working_set]


class AppGenerateMetricsPayload(Payload):
    def __init__(self, series):
        # type: (List[Series]) -> None
        super().__init__()
        self.namespace = "tracers"  # type: str
        self.lib_language = "python"  # type: str
        self.lib_version = get_version()  # type: str
        self.series = series  # type: List[Series]

    def request_type(self):
        return "app-generate-metrics"

    def to_dict(self):
        # type: () -> Dict
        return {
            "namespace": self.namespace,
            "lib_language": self.lib_language,
            "lib_version": self.lib_version,
            "series": [s.to_dict() for s in self.series],
        }


class AppClosedPayload(Payload):
    def request_type(self):
        return "app-generate-metrics"

    def to_dict(self):
        return {}
