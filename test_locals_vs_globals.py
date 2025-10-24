#!/usr/bin/env python3
"""Check if the issue is with locals vs globals"""
import os
import sys
import threading

os.environ["DD_PROFILING_ENABLED"] = "1"
os.environ["DD_PROFILING_LOCK_ENABLED"] = "1"
os.environ["DD_SERVICE"] = "test"
os.environ["DD_PROFILING_CAPTURE_PCT"] = "100"

# Patch to see what dict is being searched
from ddtrace.profiling.collector._lock import _ProfiledLock
from types import ModuleType
from ddtrace.settings.profiling import config

call_count = [0]
original_find = _ProfiledLock._find_self_name

def debug_find(self, var_dict):
    call_count[0] += 1
    dict_type = "locals" if call_count[0] % 2 == 1 else "globals"
    print(f"  [_find_self_name call #{call_count[0]}: {dict_type}]")
    print(f"    Variables: {[k for k in var_dict.keys() if not k.startswith('__')][:10]}")
    
    result = original_find(self, var_dict)
    print(f"    Result: {result!r}")
    return result

_ProfiledLock._find_self_name = debug_find

import ddtrace.profiling.auto  # noqa: E402

print("="*80)
print("Testing locals vs globals")
print("="*80)

class TestCase:
    def __init__(self):
        self.my_lock = threading.RLock()
    
    def test(self):
        print("\nAcquiring lock...")
        with self.my_lock:
            pass

test = TestCase()
test.test()

print("\n" + "="*80)
lock = test.my_lock
if isinstance(lock, _ProfiledLock):
    print(f"Final _self_name: '{lock._self_name}'")
print("="*80)

