# -*- encoding: utf-8 -*-
import gc
import os
import threading

import pytest

from ddtrace.settings.profiling import ProfilingConfig
from ddtrace.settings.profiling import _derive_default_heap_sample_size


try:
    from ddtrace.profiling.collector import _memalloc
except ImportError:
    pytestmark = pytest.mark.skip("_memalloc not available")

from ddtrace.profiling import recorder
from ddtrace.profiling.collector import memalloc


def test_heap_stress():
    # This should run for a few seconds, and is enough to spot potential segfaults.
    _memalloc.start(64, 64, 1024)

    x = []

    for _ in range(20):
        for _ in range(700):
            x.append(object())
        _memalloc.heap()
#        del x[:100]

    _memalloc.stop()
