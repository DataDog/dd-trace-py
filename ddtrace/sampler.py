from .span import MAX_TRACE_ID

class Sampler(object):
    """Sampler manages the client-side trace sampling

    Keep (100 * sample_rate)% of the traces.
    Any sampled trace should be entirely ignored by the instrumentation and won't be written.
    It samples randomly, its main purpose is to reduce the instrumentation footprint.
    """

    def __init__(self, sample_rate):
        self.sample_rate = sample_rate
        self.sampling_id_threshold = sample_rate * MAX_TRACE_ID

    def should_sample(self, span):
        return span.trace_id >= self.sampling_id_threshold
