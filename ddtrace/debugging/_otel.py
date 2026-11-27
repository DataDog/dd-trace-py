from dataclasses import dataclass
import json
from typing import Any
from typing import Optional

from ddtrace.internal.debugger_transport import DebuggerTransport
from ddtrace.internal.debugger_transport import get_debugger_transport
from ddtrace.internal.metrics import DogStatsdClient
from ddtrace.internal.metrics import MetricsClient
from ddtrace.internal.native import DebuggerTrackType


@dataclass
class EnqueuedResponse:
    accepted: bool = True
    status: Optional[int] = None
    body: str = ""


class OTLPDebuggerSender:
    agentless = False

    def __init__(self, transport: DebuggerTransport, tags: str) -> None:
        self.transport = transport
        self.tags = tags

    def send(self, payload: bytes, debugger_type: DebuggerTrackType) -> EnqueuedResponse:
        names = {
            DebuggerTrackType.Logs: "dd.debugger.log",
            DebuggerTrackType.Snapshots: "dd.debugger.snapshot",
            DebuggerTrackType.Diagnostics: "dd.debugger.diagnostic",
        }
        for record in json.loads(payload):
            self.transport.emit(names[debugger_type], record, self.tags)
        return EnqueuedResponse()

    def downgrade_to_diagnostics(self) -> bool:
        return False

    def reset_endpoints(self) -> None:
        pass


class OTLPProbeMetricsClient:
    def __init__(self, transport: DebuggerTransport, namespace: str) -> None:
        self.meter = transport.meter
        self.namespace = namespace
        self.instruments: dict[tuple[str, str], Any] = {}

    def _instrument(self, kind: str, name: str) -> Any:
        key = (kind, name)
        if key not in self.instruments:
            self.instruments[key] = getattr(self.meter, "create_" + kind)(self.namespace + "." + name)
        return self.instruments[key]

    def increment(self, name: str, value: float, tags: Optional[dict[str, str]] = None) -> None:
        self._instrument("counter", name).add(value, tags)

    def gauge(self, name: str, value: float, tags: Optional[dict[str, str]] = None) -> None:
        self._instrument("gauge", name).set(value, tags)

    def histogram(self, name: str, value: float, tags: Optional[dict[str, str]] = None) -> None:
        self._instrument("histogram", name).record(value, tags)

    def distribution(self, name: str, value: float, tags: Optional[dict[str, str]] = None) -> None:
        self.histogram(name, value, tags)


def probe_metrics_client(namespace: str) -> MetricsClient:
    transport = get_debugger_transport()
    return OTLPProbeMetricsClient(transport, namespace) if transport else DogStatsdClient(namespace=namespace)
