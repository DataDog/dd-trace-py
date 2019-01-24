import logging

from .encoding import get_encoder

log = logging.getLogger(__name__)


class Payload:
    __slots__ = ['traces', 'size', 'encoder', 'max_payload_size', 'filters']

    # Default max payload size of 5mb
    # DEV: Trace agent limit is 10mb, cutoff at 5mb to ensure we don't hit 10mb
    DEFAULT_MAX_PAYLOAD_SIZE = 5 * 1000000

    def __init__(self, encoder=None, filters=None, max_payload_size=DEFAULT_MAX_PAYLOAD_SIZE):
        self.filters = filters
        self.max_payload_size = max_payload_size
        self.encoder = encoder or get_encoder()
        self.traces = []
        self.size = 0

    def filter(self, trace):
        if not self.filters:
            return trace

        for f in self.filters:
            trace = f.process_trace(trace)
            if trace is None:
                log.debug('filter %r filtered out trace %r', f, trace)
                return None

        return trace

    def add_trace(self, trace):
        try:
            trace = self.filter(trace)
        except Exception as err:
            log.error('Error raised while trying to filter trace %r: %s', trace, err)
            return

        # Trace was filtered, return without adding
        if not trace:
            return

        encoded = self.encoder.encode_trace(trace)
        self.traces.append(encoded)
        self.size += len(encoded)
        log.debug('Trace added %r', self)

    @property
    def length(self):
        return len(self.traces)

    @property
    def empty(self):
        return self.length == 0

    @property
    def full(self):
        return self.size >= self.max_payload_size

    def get_payload(self):
        # DEV: `self.traces` is an array of encoded traces, `finish` joins them together
        return self.encoder.finish(self.traces)

    def downgrade(self, new_encoder):
        if isinstance(self.encoder, type(new_encoder)):
            return

        log.debug('Downgrading payload from %r to %r', self.encoder, new_encoder)
        self.traces = [
            new_encoder.encode(self.encoder.decode(trace))
            for trace in self.traces
        ]
        log.debug('Downgraded %d traces', len(self.traces))
        self.encoder = new_encoder

    def reset(self):
        log.debug('Resetting payload')
        self.traces = []
        self.size = 0

    def __repr__(self):
        return '{0}(length={1}, size={2}b, full={3})'.format(self.__class__.__name__, self.length, self.size, self.full)
