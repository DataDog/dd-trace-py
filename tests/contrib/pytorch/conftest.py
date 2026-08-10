"""Shared fixtures for the pytorch integration test suite."""

from unittest import mock

import pytest

from ddtrace.contrib.internal.pytorch import _device
from ddtrace.contrib.internal.pytorch import _distributed
from ddtrace.contrib.internal.pytorch import _test_helpers as _th


# Non-deterministic tags to ignore in snapshot tests (follow Ray's pattern).
PYTORCH_SNAPSHOT_IGNORES = [
    "meta.tracer_version",
    "meta.runtime-id",
    "metrics._dd.top_level",
    "metrics._dd.tracer_kr",
    "metrics._sampling_priority_v1",
    "metrics.process_id",
    "name",
    "resource",
    "service",
    "start",
    "duration",
]


@pytest.fixture
def pytorch_clean_state():
    """Reset rank-root span, device cache, and distributed context.

    Compose into autouse fixtures in each test module. Resets _rank_ctx so
    tests that call into _distributed directly don't leak ExecutionContext
    across test boundaries.
    """
    _distributed._rank_ctx.set(None)
    _th.reset_device_cache()
    _th.close_rank_root()
    with (
        mock.patch.object(_device, "_cuda_is_available", return_value=False),
        mock.patch.object(_device, "_hostname", return_value="h-9"),
    ):
        _device.discover(local_rank=0)
    yield
    _th.close_rank_root()
    _th.reset_device_cache()
    _distributed._rank_ctx.set(None)
