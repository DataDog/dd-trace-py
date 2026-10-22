from enum import Enum
from typing import Optional


class ExperimentationOptOutMode(str, Enum):
    """Controls per-subject exclusion from experimentation."""

    DEFAULT = "default"
    DISABLED = "disabled"
    IMMEDIATE = "immediate"


def resolve_mode(mode: Optional[ExperimentationOptOutMode], configured: str) -> ExperimentationOptOutMode:
    """Resolve the explicit override before the central SDK setting."""
    if mode in (ExperimentationOptOutMode.DISABLED, ExperimentationOptOutMode.IMMEDIATE):
        return ExperimentationOptOutMode(mode)
    if mode is not None and mode != ExperimentationOptOutMode.DEFAULT:
        raise ValueError("Unsupported experimentation opt-out mode")
    value = configured.strip().lower()
    if value in ("", "disabled"):
        return ExperimentationOptOutMode.DISABLED
    if value == "immediate":
        return ExperimentationOptOutMode.IMMEDIATE
    raise ValueError("DD_FEATURE_FLAGS_EXPERIMENTATION_OPT_OUT_MODE must be disabled or immediate")
