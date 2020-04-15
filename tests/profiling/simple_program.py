import sys
import time

from ddtrace.profiling import _service
from ddtrace.profiling import bootstrap
from ddtrace.profiling.collector import stack

for running_collector in bootstrap.profiler.collectors:
    if isinstance(running_collector, stack.StackCollector):
        break
else:
    assert False, "Unable to find stack collector"


print("hello world")
assert running_collector.status == _service.ServiceStatus.RUNNING
print(running_collector.interval)

t0 = time.time()
while time.time() - t0 < (running_collector.interval * 10):
    pass

# Do some serious memory allocations!
for x in range(5000000):
    object()

print(len(running_collector.recorder.events[stack.StackSampleEvent]))
sys.exit(42)
