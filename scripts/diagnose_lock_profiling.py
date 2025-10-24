#!/usr/bin/env python3
"""
Detailed diagnostic for lock profiling - why are lock names missing?
"""
import os
import sys
import threading
import time

# Configure BEFORE importing ddtrace
os.environ["DD_PROFILING_ENABLED"] = "1"
os.environ["DD_PROFILING_LOCK_ENABLED"] = "1"
os.environ["DD_PROFILING_CAPTURE_PCT"] = "100"
os.environ["DD_SERVICE"] = "lock-diagnostic"
os.environ["DD_PROFILING_OUTPUT_PPROF"] = "/tmp/lock_diagnostic"

print("="*80)
print("Lock Profiling Diagnostic")
print("="*80)

# Import profiler
import ddtrace.profiling.auto  # noqa: E402

print("\n1. Checking collector status...")
try:
    from ddtrace.profiling import profiler as profiler_module
    from ddtrace.profiling.collector.threading import ThreadingLockCollector
    
    if hasattr(profiler_module, '_profiler') and profiler_module._profiler is not None:
        prof = profiler_module._profiler
        print(f"   ✓ Profiler exists, status: {prof.status}")
        
        # Check for lock collector
        lock_collectors = [c for c in prof._collectors if isinstance(c, ThreadingLockCollector)]
        if lock_collectors:
            print(f"   ✓ Found {len(lock_collectors)} ThreadingLockCollector(s)")
            for lc in lock_collectors:
                print(f"      - capture_pct: {lc._capture_sampler.capture_pct}")
                print(f"      - nframes: {lc.nframes}")
                print(f"      - endpoint_collection: {lc.endpoint_collection_enabled}")
                print(f"      - original: {lc._original}")
        else:
            print("   ✗ NO ThreadingLockCollector found!")
            print("   Available collectors:")
            for c in prof._collectors:
                print(f"      - {c.__class__.__name__}")
    else:
        print("   ✗ Profiler not initialized")
except Exception as e:
    print(f"   ⚠️  Error: {e}")
    import traceback
    traceback.print_exc()

print("\n2. Checking if Lock is patched...")
try:
    import threading
    print(f"   threading.Lock type: {type(threading.Lock)}")
    print(f"   threading.Lock: {threading.Lock}")
    
    # Create a lock and see what type it is
    test_lock = threading.Lock()
    print(f"   Created lock type: {type(test_lock)}")
    print(f"   Created lock: {test_lock}")
    
    # Check if it's wrapped
    if hasattr(test_lock, '__wrapped__'):
        print(f"   ✓ Lock is wrapped!")
        print(f"   Wrapped object: {test_lock.__wrapped__}")
    else:
        print(f"   ✗ Lock is NOT wrapped (no __wrapped__ attribute)")
        
    # Check for _self_name attribute (from _ProfiledLock)
    if hasattr(test_lock, '_self_name'):
        print(f"   ✓ Has _self_name attribute: {test_lock._self_name}")
    else:
        print(f"   ✗ No _self_name attribute")
        
except Exception as e:
    print(f"   ⚠️  Error: {e}")
    import traceback
    traceback.print_exc()

print("\n3. Creating named locks and using them...")
my_named_lock = threading.Lock()
another_lock = threading.Lock()

print(f"   Created my_named_lock: {type(my_named_lock)}")
print(f"   Created another_lock: {type(another_lock)}")

# Use the locks
for i in range(10):
    with my_named_lock:
        time.sleep(0.001)
    
    with another_lock:
        time.sleep(0.001)

print("   ✓ Used locks 10 times each")

print("\n4. Forcing profile upload...")
try:
    from ddtrace.internal.datadog.profiling import ddup
    ddup.upload()  # type: ignore
    print("   ✓ ddup.upload() called")
except Exception as e:
    print(f"   ⚠️  Error: {e}")

print("\n5. Waiting for profile write...")
time.sleep(5)

print("\n6. Parsing pprof file...")
try:
    import glob
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
    from tests.profiling.collector import pprof_utils
    
    pprof_pattern = "/tmp/lock_diagnostic.*"
    files = glob.glob(pprof_pattern)
    
    if not files:
        print(f"   ✗ No pprof files found: {pprof_pattern}")
    else:
        print(f"   ✓ Found {len(files)} pprof file(s)")
        
        profile = pprof_utils.parse_newest_profile("/tmp/lock_diagnostic")
        
        print(f"\n   Profile summary:")
        print(f"   - Total samples: {len(profile.sample)}")
        print(f"   - Sample types: {[profile.string_table[st.type] for st in profile.sample_type]}")
        
        # Look for lock samples
        lock_acquire_samples = []
        lock_release_samples = []
        
        sample_types = {profile.string_table[st.type]: idx for idx, st in enumerate(profile.sample_type)}
        
        for sample in profile.sample:
            if "lock-acquire" in sample_types:
                idx = sample_types["lock-acquire"]
                if sample.value[idx] > 0:
                    lock_acquire_samples.append(sample)
            
            if "lock-release" in sample_types:
                idx = sample_types["lock-release"]
                if sample.value[idx] > 0:
                    lock_release_samples.append(sample)
        
        print(f"   - Lock acquire samples: {len(lock_acquire_samples)}")
        print(f"   - Lock release samples: {len(lock_release_samples)}")
        
        if lock_acquire_samples or lock_release_samples:
            print(f"\n   ✓ Lock samples exist!")
            
            # Examine first acquire sample in detail
            if lock_acquire_samples:
                sample = lock_acquire_samples[0]
                print(f"\n   First lock-acquire sample details:")
                print(f"   - Values: {sample.value}")
                print(f"   - Labels ({len(sample.label)}):")
                
                for label in sample.label:
                    key = profile.string_table[label.key]
                    if label.str:
                        val = profile.string_table[label.str]
                    elif label.num:
                        val = label.num
                    else:
                        val = "(empty)"
                    print(f"      {key}: {val}")
                
                # Check for lock-name specifically
                lock_name_labels = [l for l in sample.label if profile.string_table[l.key] == "lock-name"]
                if lock_name_labels:
                    lock_name = profile.string_table[lock_name_labels[0].str]
                    print(f"\n   ✓ LOCK NAME FOUND: {lock_name}")
                else:
                    print(f"\n   ✗ NO 'lock-name' label found!")
                
                # Check locations
                print(f"\n   Location IDs: {sample.location_id}")
                if sample.location_id:
                    loc_id = sample.location_id[0]
                    location = next((loc for loc in profile.location if loc.id == loc_id), None)
                    if location:
                        for line in location.line:
                            func = next((f for f in profile.function if f.id == line.function_id), None)
                            if func:
                                func_name = profile.string_table[func.name]
                                file_name = profile.string_table[func.filename]
                                print(f"   Function: {func_name} in {file_name}:{line.line}")
        else:
            print(f"\n   ✗ NO lock samples found at all!")
            print(f"\n   This means:")
            print(f"   - Lock collector might not be running")
            print(f"   - Locks might not be patched")
            print(f"   - Capture percentage might be 0")
            
except Exception as e:
    print(f"   ✗ Error parsing profile: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "="*80)
print("Diagnostic Complete")
print("="*80)

print("\nKey Questions:")
print("1. Is ThreadingLockCollector present? (Check step 1)")
print("2. Is threading.Lock wrapped? (Check step 2)")
print("3. Are lock samples in the profile? (Check step 6)")
print("4. Do lock samples have 'lock-name' labels? (Check step 6)")

