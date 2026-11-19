"""Deterministic rate sampling shared by tracing and security products."""

from ddtrace.internal.constants import MAX_UINT_64BITS
from ddtrace.internal.constants import SAMPLING_HASH_MODULO
from ddtrace.internal.constants import SAMPLING_KNUTH_FACTOR


class RateSampler:
    """Deterministically sample identifiers at a configured rate."""

    def __init__(self, sample_rate: float = 1.0) -> None:
        """sample_rate is clamped between 0 and 1 inclusive"""
        sample_rate = min(1, max(0, sample_rate))
        self.set_sample_rate(sample_rate)

    def set_sample_rate(self, sample_rate: float) -> None:
        self.sample_rate = float(sample_rate)
        self.sampling_id_threshold = self.sample_rate * MAX_UINT_64BITS

    def sample(self, sampling_id: int) -> bool:
        """Sample a caller-provided 64-bit identifier."""
        sampled = ((sampling_id * SAMPLING_KNUTH_FACTOR) % SAMPLING_HASH_MODULO) <= self.sampling_id_threshold
        return sampled

    def __repr__(self) -> str:
        return f"RateSampler(sample_rate={self.sample_rate})"
