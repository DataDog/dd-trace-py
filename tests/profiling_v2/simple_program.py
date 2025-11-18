#!/usr/bin/env python
import os
import sys

from ddtrace.internal import service
from ddtrace.profiling import bootstrap
from ddtrace.profiling.collector import stack


for running_collector in bootstrap.profiler._profiler._collectors:
    if isinstance(running_collector, stack.StackCollector):
        break
else:
    raise AssertionError("Unable to find stack collector")


print("hello world")
assert running_collector.status == service.ServiceStatus.RUNNING

# Do some serious memory allocations!
for _ in range(5000000):
    object()

print(os.getpid())
sys.exit(42)
