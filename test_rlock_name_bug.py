#!/usr/bin/env python3
"""
Minimal reproduction showing RLock name discovery bug
"""
import os
import sys
import threading
import time

os.environ["DD_PROFILING_ENABLED"] = "1"
os.environ["DD_PROFILING_LOCK_ENABLED"] = "1"
os.environ["DD_SERVICE"] = "test"
os.environ["DD_PROFILING_CAPTURE_PCT"] = "100"

import ddtrace.profiling.auto  # noqa: E402
from ddtrace.profiling.collector._lock import _ProfiledLock

# Track what names are discovered
discovered_names = []

original_maybe_update = _ProfiledLock._maybe_update_self_name
def tracking_maybe_update(self):
    original_maybe_update(self)
    discovered_names.append((self._self_init_loc, self._self_name))
_ProfiledLock._maybe_update_self_name = tracking_maybe_update

print("="*80)
print("RLock Name Discovery Test")
print("="*80)

# Test Case 1: Simple method (expected: 'foo_lock')
class SimpleCase:
    def __init__(self):
        self.foo_lock = threading.RLock()
    
    def test(self):
        with self.foo_lock:
            pass

print("\nTest 1: Simple method")
simple = SimpleCase()
simple.test()

# Test Case 2: Method with parameters (like stress test)
class StressTestCase:
    def __init__(self):
        self.shared_resource_lock = threading.RLock()
        self.data = []
    
    def workload(self, worker_id: int, iterations: int):
        for i in range(iterations):
            with self.shared_resource_lock:
                self.data.append(f"item-{i}")

print("\nTest 2: Method with parameters and for loop (like stress test)")
stress = StressTestCase()
stress.workload(worker_id=1, iterations=2)

print("\n" + "="*80)
print("Discovered Lock Names:")
for init_loc, name in discovered_names:
    print(f"  {init_loc}: '{name}'")

print("\n" + "="*80)
print("Expected:")
print("  test_rlock_name_bug.py:XX: 'foo_lock'")
print("  test_rlock_name_bug.py:XX: 'shared_resource_lock'")
print("\nActual behavior:")
if any('foo_lock' in name for _, name in discovered_names):
    print("  ✓ Simple case works")
else:
    print("  ✗ Simple case BROKEN - got 'self' instead of 'foo_lock'")

if any('shared_resource_lock' in name for _, name in discovered_names):
    print("  ✓ Stress test pattern works")
else:
    print("  ✗ Stress test pattern BROKEN - got 'self' or empty instead of 'shared_resource_lock'")
print("="*80)
