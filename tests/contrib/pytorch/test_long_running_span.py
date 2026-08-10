"""Tests for the pytorch.rank span rotation / long-running lifecycle.

Follows the same pattern as tests/contrib/ray/test_long_running_span.py:
rotation interval is patched to 0 (fires immediately) so tests run
without real 600-second waits.
"""

import time
from unittest import mock

import pytest

from ddtrace.contrib.internal.pytorch import _distributed
import ddtrace.contrib.internal.pytorch._rank_root as rr


@pytest.fixture(autouse=True)
def _reset(tracer, pytorch_clean_state):  # noqa: F811
    """Autouse wrapper: pulls in the shared pytorch_clean_state fixture."""


def test_rotation_fires_and_replaces_span(_reset):
    """After _rotation_interval_s elapses the span is replaced."""
    with mock.patch.object(rr, "_rotation_interval_s", 0):  # fire immediately
        rr.open_rank_span(rank=0, world_size=1, framework="ddp", training_job_id="job-1")
        first_span = rr._span
        time.sleep(0.2)

    second_span = rr._span
    assert second_span is not first_span, "span was not rotated"
    assert first_span.finished, "old span should be finished after rotation"
    assert second_span is not None


def test_rotation_tags_old_span_was_long_running(_reset):
    """Rotated spans carry _dd.was_long_running=1."""
    with mock.patch.object(rr, "_rotation_interval_s", 0):
        rr.open_rank_span(rank=0, world_size=1, framework="ddp", training_job_id="job-1")
        first_span = rr._span
        time.sleep(0.2)

    assert first_span.get_metric("_dd.was_long_running") == 1


def test_close_cancels_rotation_timer(_reset):
    """close() cancels the pending rotation timer."""
    rr.open_rank_span(rank=0, world_size=1, framework="ddp", training_job_id="job-1")
    assert rr._rotation_timer is not None
    rr.close()
    assert rr._rotation_timer is None


def test_subgroup_destroy_does_not_close_rank_span(_reset):
    """Destroying a subgroup must not close the pytorch.rank span."""
    rr.open_rank_span(rank=0, world_size=2, framework="ddp", training_job_id="job-1")
    original_span = rr._span

    fake_group = object()
    with mock.patch("torch.distributed.destroy_process_group") as mock_destroy:
        mock_destroy.return_value = None
        _distributed._wrapped_destroy_process_group(mock_destroy, None, (fake_group,), {})

    assert rr._span is original_span, "subgroup destroy must not close the rank span"
    assert not original_span.finished
