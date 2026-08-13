"""Deterministic CI workload modeling and shard allocation."""

from .planner import AllocationError
from .planner import build_suite_plan
from .planner import legacy_round_robin
from .planner import weighted_lpt
from .suites import SuiteVenvInfo
from .suites import calculate_parallelism_from_venvs
from .suites import collect_all_suite_venv_info
from .suites import compute_parallelism
from .suites import compute_runtime_parallelism
from .suites import scale_suites


__all__ = [
    "AllocationError",
    "SuiteVenvInfo",
    "build_suite_plan",
    "calculate_parallelism_from_venvs",
    "collect_all_suite_venv_info",
    "compute_parallelism",
    "compute_runtime_parallelism",
    "legacy_round_robin",
    "scale_suites",
    "weighted_lpt",
]
