"""Quick test to verify exception profiling flag works"""

import time

from ddtrace.profiling import Profiler


# Test 1: With exception profiling enabled (default)
print("Test 1: Exception profiling ENABLED (should abort)")
prof = Profiler(_exception_profiling_enabled=True)
prof.start()
time.sleep(0.1)  # Give it time to sample
prof.stop()
print("Test 1 completed (shouldn't reach here if abort works)")

# Test 2: With exception profiling disabled
print("\nTest 2: Exception profiling DISABLED (should NOT abort)")
prof2 = Profiler(_exception_profiling_enabled=False)
prof2.start()
time.sleep(0.1)
prof2.stop()
print("Test 2 completed successfully")
