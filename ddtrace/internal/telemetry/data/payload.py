from typing import Dict
from typing import List

from ....version import get_version
from .dependency import Dependency
from .dependency import create_dependency
from .integration import Integration
from .metrics import Series


class Payload:
    REQUEST_TYPE = "not-defined"  # type: str

    def to_dict(self):
        # type: () -> Dict
        return {}


class AppIntegrationsChangedPayload(Payload):
    REQUEST_TYPE = "app-integrations-changed"  # type: str

    def __init__(self, integrations):
        # type: (List[Integration]) -> None
        super().__init__()
        self.integrations = integrations  # type: List[Integration]

    def to_dict(self):
        # type: () -> Dict
        return {"integrations": self.integrations}


class AppStartedPayload(Payload):
    REQUEST_TYPE = "app-started"  # type: str

    def __init__(self, configurations={}, additional_payload={}):
        # type: (Dict, Dict) -> None
        super().__init__()
        self.configuration = configurations  # type: Dict[str, str]
        self.additional_payload = additional_payload  # type: Dict[str, str]
        self.dependencies = self.get_dependencies()

    def get_dependencies(self):
        # type: () -> List[Dependency]
        import pkg_resources

        dependencies = []
        return [
            create_dependency(pkg.project_name, pkg.version)
            for pkg in pkg_resources.working_set
        ]

    def to_dict(self):
        # type: () -> Dict
        return {
            "configuration": self.configuration,
            "additional_payload": self.additional_payload,
            "dependencies": self.dependencies,
        }


class AppGenerateMetricsPayload(Payload):
    REQUEST_TYPE = "app-generate-metrics"  # type: str

    def __init__(self, series):
        # type: (List[Series]) -> None
        super().__init__()
        self.namespace = "tracers"  # type: str
        self.lib_language = "python"  # type: str
        self.lib_version = get_version()  # type: str
        self.series = series  # type: List[Series]

    def to_dict(self):
        # type: () -> Dict
        return {
            "namespace": self.namespace,
            "lib_language": self.lib_language,
            "lib_version": self.lib_version,
            "series": [s.to_dict() for s in self.series],
        }


class AppClosedPayload(Payload):
    REQUEST_TYPE = "app-closing"  # type: str
