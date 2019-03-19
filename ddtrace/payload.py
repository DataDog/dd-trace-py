import logging

from .encoding import get_encoder

log = logging.getLogger(__name__)


class Payload:
    """
    Trace agent API payload buffer class

    This class is used to encoded and store traces to build the payload we send to
    the trace agent.

    DEV: We encoded and buffer traces so that we can reliable determine the size of
         the payload easily so we can flush based on the payload size.
    """
    __slots__ = ('traces', 'size', 'encoder', 'max_payload_size')

    # Default max payload size of 5mb
    # DEV: Trace agent limit is 10mb, cutoff at 5mb to ensure we don't hit 10mb
    DEFAULT_MAX_PAYLOAD_SIZE = 5 * 1000000

    def __init__(self, encoder=None, max_payload_size=DEFAULT_MAX_PAYLOAD_SIZE):
        """
        Constructor for Payload

        :param encoder: The encoded to use, default is the default encoder
        :type encoder: ``ddtrace.encoding.Encoder``
        :param max_payload_size: The max number of bytes a payload should be before
            being considered full (default: 5mb)
        """
        self.max_payload_size = max_payload_size
        self.encoder = encoder or get_encoder()
        self.traces = []
        self.size = 0

    def add_trace(self, trace):
        """
        Encode and append a trace to this payload

        :param trace: A trace to append
        :type trace: A list of ``ddtrace.span.Span``s
        """
        # No trace or empty trace was given, ignore
        if not trace:
            return

        # Encode the trace, append, and add it's length to the size
        encoded = self.encoder.encode_trace(trace)
        self.traces.append(encoded)
        self.size += len(encoded)

    @property
    def length(self):
        """
        Get the number of traces in this payload

        :returns: The number of traces in the payload
        :rtype: int
        """
        return len(self.traces)

    @property
    def empty(self):
        """
        Whether this payload is empty or not

        :returns: Whether this payload is empty or not
        :rtype: bool
        """
        return self.length == 0

    @property
    def full(self):
        """
        Whether this payload is at or over the max allowed payload size

        :returns: Whether we have reached the max payload size yet or not
        :rtype: bool
        """
        return self.size >= self.max_payload_size

    def get_payload(self):
        """
        Get the fully encoded payload

        :returns: The fully encoded payload
        :rtype: str | bytes
        """
        # DEV: `self.traces` is an array of encoded traces, `join_encoded` joins them together
        return self.encoder.join_encoded(self.traces)

    def __repr__(self):
        """Get the string representation of this payload"""
        return '{0}(length={1}, size={2}b, full={3})'.format(self.__class__.__name__, self.length, self.size, self.full)
