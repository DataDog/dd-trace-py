"""Tests for _cuda_visible_to_physical device index remapping."""

import os
from unittest import mock

from ddtrace.contrib.internal.pytorch import _device


def test_cuda_visible_to_physical_no_remapping():
    with mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop("CUDA_VISIBLE_DEVICES", None)
        assert _device._cuda_visible_to_physical(1) == 1


def test_cuda_visible_to_physical_remapping():
    with mock.patch.dict(os.environ, {"CUDA_VISIBLE_DEVICES": "2,4,6"}):
        assert _device._cuda_visible_to_physical(0) == 2
        assert _device._cuda_visible_to_physical(1) == 4


def test_cuda_visible_to_physical_no_dev_files():
    with mock.patch.dict(os.environ, {"CUDA_VISIBLE_DEVICES": "NoDevFiles"}):
        assert _device._cuda_visible_to_physical(0) == 0


def test_cuda_visible_to_physical_uuid_falls_back():
    with mock.patch.dict(os.environ, {"CUDA_VISIBLE_DEVICES": "GPU-abc,GPU-def"}):
        # UUID entries are not integers; function falls back to visible_idx
        assert _device._cuda_visible_to_physical(1) == 1
