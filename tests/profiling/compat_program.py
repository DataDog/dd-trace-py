import sys
import time

import ddtrace.profile.auto  # noqa

from ddtrace.profile import bootstrap
from ddtrace.profile import collector  # noqa
from ddtrace.profile.collector import stack

for running_collector in bootstrap.profiler.collectors:
    if isinstance(running_collector, stack.StackCollector):
        break
else:
    assert False, "Unable to find stack collector"


print("hello world")
print(running_collector.interval)

t0 = time.time()
while time.time() - t0 < (running_collector.interval * 10):
    pass

# Do some serious memory allocations!
for x in range(5000000):
    object()

print(len(running_collector.recorder.events[stack.StackSampleEvent]))
sys.exit(42)
