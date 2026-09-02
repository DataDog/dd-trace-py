from ddtrace.internal.native._native import Context


def _update_otel_sampling_decision(
    context: Context, sampled: bool, sample_rate: float, probabilistic_decision: bool
) -> None:
    """Record the decision needed to build canonical trace-level ot= state."""
    context._update_otel_sampling_decision(sampled, sample_rate, probabilistic_decision)


__all__ = ["Context"]
