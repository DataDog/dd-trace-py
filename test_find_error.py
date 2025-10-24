#!/usr/bin/env python3
"""Find what's causing _find_self_name to return 'self'"""
import os
import sys
import threading

os.environ["DD_PROFILING_ENABLED"] = "1"
os.environ["DD_PROFILING_LOCK_ENABLED"] = "1"
os.environ["DD_SERVICE"] = "test"
os.environ["DD_PROFILING_CAPTURE_PCT"] = "100"

# Patch _find_self_name to see what's happening
from ddtrace.profiling.collector._lock import _ProfiledLock
from types import ModuleType
from ddtrace.settings.profiling import config

def debug_find_self_name(self, var_dict):
    for name, value in var_dict.items():
        if name.startswith("__") or isinstance(value, ModuleType):
            continue
        
        is_match = (value is self)
        print(f"  Check var '{name}': is_match={is_match}, type={type(value).__name__}")
        
        if is_match:
            print(f"    -> RETURNING '{name}' (direct match)")
            return name
        
        if config.lock.name_inspect_dir:
            print(f"    -> Inspecting attributes of '{name}'...")
            errors = []
            for attribute in dir(value):
                if attribute.startswith("__"):
                    continue
                try:
                    attr_value = getattr(value, attribute)
                    if attr_value is self:
                        print(f"       MATCH! {name}.{attribute} is the lock")
                        self._self_name = attribute
                        return attribute
                except Exception as e:
                    errors.append((attribute, str(e)))
            
            if errors:
                print(f"       Errors getting attributes: {len(errors)} errors")
                for attr, err in errors[:3]:
                    print(f"         - {attr}: {err[:50]}")
    
    print(f"  No match found, returning None")
    return None

_ProfiledLock._find_self_name = debug_find_self_name

import ddtrace.profiling.auto  # noqa: E402

print("="*80)
print("Debugging _find_self_name")
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

print("="*80)

