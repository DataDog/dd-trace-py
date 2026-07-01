from typing import Optional

from ddtrace.internal.settings._core import DDConfig

class AgentConfig(DDConfig):
    trace_agent_url: str
    dogstatsd_url: str
    trace_agent_timeout_seconds: float
    trace_native_span_events: bool
    _trace_native_span_events: Optional[bool]
    _trace_agent_hostname: Optional[str]
    _trace_agent_port: Optional[int]
    _trace_agent_url: Optional[str]
    _dogstatsd_host: Optional[str]
    _dogstatsd_port: Optional[int]
    _dogstatsd_url: Optional[str]
    _agent_host: Optional[str]
    _agent_port: Optional[int]

config: AgentConfig

def get_agent_hostname() -> str: ...
def get_agent_port() -> int: ...
