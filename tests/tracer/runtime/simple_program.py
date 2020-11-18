import sys
import time

from ddtrace import tracer
tracer._RUNTIME_METRICS_INTERVAL = 1. / 4
tracer.configure(collect_metrics=True)

runtime_worker = tracer._runtime_worker

print("hello world")
assert runtime_worker.is_alive()

t0 = time.time()
while time.time() - t0 < (tracer._RUNTIME_METRICS_INTERVAL * 1.5):
    pass

sys.exit(42)
