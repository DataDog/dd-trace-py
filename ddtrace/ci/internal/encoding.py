import msgpack

from ddtrace.internal._encoding import packb
from ddtrace.internal.encoding import _EncoderBase


class AgentlessEncoderV1(_EncoderBase):
    """Encode spans for use in Agentless CI Visibility intake endpoint."""
    
    def __init__(self, metadata=None, *args):
        # type: (Optional[Dict[str, str]], *Any) -> None
        super(AgentlessEncoderV1, self).__init__()
        self.metadata = metadata or {}

    @staticmethod
    def _convert_span(span):
        # type: (Span) -> Dict[str, Any]
        """Converts a span to the event format."""
        return {
            "type": "test" if span.span_type == 'test' else 'span',
            "version": 1,
            "content": _EncoderBase._span_to_dict(span),
        }

    def encode_traces(self, traces):
        # type: (List[List[Span]]) -> str
        """
        Encodes a list of traces, expecting a list of items where each items
        is a list of spans. Before dumping the string in a serialized format all
        traces are normalized according to the encoding format. The trace
        nesting is not changed.

        :param traces: A list of traces that should be serialized
        """
        # type: (List[List[Span]]) -> str
        normalized_traces = [self._convert_span(span) for trace in traces for span in trace]
        return self.encode({
            "version": 1,
            "metadata": self.metadata,
            "events": normalized_traces,
        })

    def encode(self, obj):
        # type: (Dict[str, Any]) -> str
        """
        Defines the underlying format used during traces or services encoding.
        This method must be implemented and should only be used by the internal
        functions.
        """
        return packb(obj)

    @staticmethod
    def decode(data):
        return msgpack.unpackb(data, raw=True, strict_map_key=False)
