from .._encoding import BufferedEncoder
from ..encoding import AgentlessTraceJSONEncoder


class WriterClientBase(object):
    """A class encapsulating an endpoint/encoder pair that a TraceWriter can send payloads to"""

    ENDPOINT = ""

    def __init__(
        self,
        encoder: BufferedEncoder,
    ):
        self.encoder = encoder


class AgentlessWriterClient(WriterClientBase):
    """Client for the agentless span intake (api/v2/spans)."""

    ENDPOINT = "api/v2/spans"

    def __init__(self, buffer_size: int, max_payload_size: int) -> None:
        super(AgentlessWriterClient, self).__init__(
            AgentlessTraceJSONEncoder(max_size=buffer_size, max_item_size=max_payload_size)
        )


# Trace agent API versions the native writer can encode traces for.
SUPPORTED_API_VERSIONS = frozenset({"v0.4", "v0.5"})
DEFAULT_API_VERSION = "v0.5"
