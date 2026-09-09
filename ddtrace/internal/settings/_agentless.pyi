from typing import Any
from typing import Optional

from ddtrace.internal.settings._core import DDConfig

class AgentlessConfig(DDConfig):
    api_key: Optional[str]
    site: str
    enabled: bool
    _apm_tracing: Optional[bool]
    _ci_visibility: Optional[bool]
    _llmobs: Optional[bool]
    apm_tracing: bool
    ci_visibility: bool
    llmobs: Optional[bool]
    any_enabled: bool
    def reported_configuration(self) -> list[tuple[str, Any, str]]: ...

config: AgentlessConfig
