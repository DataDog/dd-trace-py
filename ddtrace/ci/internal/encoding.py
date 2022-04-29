import os
import uuid
import ddtrace
from ddtrace.internal import compat
from ddtrace.internal._encoding import ListBufferedEncoder, packb
from ddtrace.internal.encoding import _EncoderBase


class AgentlessEncoderV1(ListBufferedEncoder):
    """Encode spans for use in Agentless CI Visibility intake endpoint."""

    content_type = "application/msgpack"

    def __init__(self, max_size, max_item_size):
        # type: (int, int) -> None
        super(AgentlessEncoderV1, self).__init__()
        self.metadata = {
            "*": {
                "language": "python",
                "library_version": ddtrace.__version__,
                "language_version": compat.PYTHON_VERSION,
                "runtime-id": uuid.uuid4().hex,
                "env": os.environ.get("DD_ENV", "none"),
            }
        }

    def encode_item(self, trace):
        # type: (Trace) -> Dict[str, Any]
        """Converts a span to the event format."""
        return [{
            "type": "test" if span.span_type == "test" else "span",
            "version": 1,
            "content": _EncoderBase._span_to_dict(span),
        } for span in trace]

    def encode(self):
        # type: () -> str
        """
        Defines the underlying format used during traces or services encoding.
        This method must be implemented and should only be used by the internal
        functions.
        """
        events = self.get()
        if not events:
            return
        
        data = {
            "version": 1,
            "metadata": self.metadata or {},
            "events": [span for trace in events for span in trace],
        }
        return packb(data)
