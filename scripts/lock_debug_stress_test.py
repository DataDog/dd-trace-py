#!/usr/bin/env python3
"""
Lock Debug Stress Test

Debug version for threading.Lock() that writes BOTH local pprof files AND uploads to production.
Use this to debug discrepancies between what the collector captures vs what appears in UI.

Usage:
    export DD_AGENT_HOST="localhost"
    python scripts/lock_debug_stress_test.py
"""
import glob
import os
import sys
import threading
import time

# Configure profiler BEFORE importing ddtrace
os.environ["DD_PROFILING_ENABLED"] = "1"
os.environ["DD_PROFILING_LOCK_ENABLED"] = "1"
os.environ["DD_SERVICE"] = "lock-debug-stress-test"
os.environ["DD_ENV"] = "debug"
os.environ["DD_VERSION"] = "1.0.0"
os.environ["DD_PROFILING_CAPTURE_PCT"] = "100"

# IMPORTANT: Write local pprof files in addition to uploading
os.environ["DD_PROFILING_OUTPUT_PPROF"] = "/tmp/lock_debug_stress_test"

print("\n" + "="*80)
print("Lock Debug Stress Test - Local + Production")
print("="*80)
print(f"Service: {os.environ['DD_SERVICE']}")
print(f"Local pprof files: {os.environ['DD_PROFILING_OUTPUT_PPROF']}")
print("="*80 + "\n")

# Import profiler auto-start
import ddtrace.profiling.auto  # noqa: E402

# Import test utilities for parsing pprof
try:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
    from tests.profiling.collector import pprof_utils
    PPROF_UTILS_AVAILABLE = True
    print("✓ pprof parsing utilities loaded")
except ImportError as e:
    print(f"⚠️  Warning: Could not import pprof_utils: {e}")
    PPROF_UTILS_AVAILABLE = False


# ============================================================================
# Simple Test Workloads
# ============================================================================

def workload_local_locks(worker_id: int, iterations: int):
    """Local named locks"""
    for i in range(iterations):
        request_lock = threading.Lock()
        response_lock = threading.Lock()
        
        with request_lock:
            time.sleep(0.001)
        
        with response_lock:
            time.sleep(0.001)


class ServiceManager:
    """Class with member locks"""
    def __init__(self):
        self.auth_lock = threading.Lock()
        self.cache_lock = threading.Lock()
    
    def authenticate(self):
        with self.auth_lock:
            time.sleep(0.001)
    
    def update_cache(self):
        with self.cache_lock:
            time.sleep(0.001)


def workload_class_locks(worker_id: int, iterations: int):
    """Class member locks"""
    manager = ServiceManager()
    for i in range(iterations):
        manager.authenticate()
        manager.update_cache()


# Global lock
global_database_lock = threading.Lock()


def workload_global_lock(worker_id: int, iterations: int):
    """Global lock"""
    global global_database_lock
    for i in range(iterations):
        with global_database_lock:
            time.sleep(0.001)


# ============================================================================
# Main Test
# ============================================================================

def run_workloads(duration: int = 30, num_workers: int = 4):
    """Run all workloads"""
    print("\n" + "="*80)
    print(f"Running workloads for {duration} seconds with {num_workers} workers")
    print("="*80 + "\n")
    
    threads = []
    end_time = time.time() + duration
    
    def worker_wrapper(worker_id: int, workload_func):
        """Run workload until time expires"""
        while time.time() < end_time:
            workload_func(worker_id, 20)
    
    # Start workers for each workload type
    workloads = [
        ("LocalLocks", workload_local_locks),
        ("ClassLocks", workload_class_locks),
        ("GlobalLock", workload_global_lock),
    ]
    
    for workload_name, workload_func in workloads:
        for worker_id in range(num_workers):
            t = threading.Thread(
                target=worker_wrapper,
                args=(worker_id, workload_func),
                name=f"{workload_name}-Worker-{worker_id}"
            )
            threads.append(t)
            t.start()
    
    print(f"Started {len(threads)} worker threads")
    
    # Show progress
    start_time = time.time()
    while time.time() < end_time:
        elapsed = time.time() - start_time
        remaining = duration - elapsed
        print(f"  Running... {elapsed:.0f}s elapsed, {remaining:.0f}s remaining", end="\r")
        time.sleep(1)
    
    print("\n\nWaiting for threads to complete...")
    for t in threads:
        t.join()
    
    print("✓ All threads completed\n")


def analyze_local_pprof():
    """Parse local pprof files and analyze lock names"""
    if not PPROF_UTILS_AVAILABLE:
        print("Cannot analyze local pprof files (pprof_utils not available)")
        return
    
    print("\n" + "="*80)
    print("Analyzing Local pprof Files")
    print("="*80 + "\n")
    
    pprof_pattern = os.environ["DD_PROFILING_OUTPUT_PPROF"] + ".*"
    pprof_files = sorted(glob.glob(pprof_pattern), key=os.path.getmtime)
    
    if not pprof_files:
        print(f"⚠️  No pprof files found matching: {pprof_pattern}")
        return
    
    print(f"Found {len(pprof_files)} pprof file(s)")
    
    # Analyze the newest file
    pprof_prefix = os.environ["DD_PROFILING_OUTPUT_PPROF"]
    print(f"\nAnalyzing newest profile with prefix: {pprof_prefix}")
    print("-" * 80)
    
    try:
        # Use the existing parse function (pprof_utils imported at top if available)
        from tests.profiling.collector import pprof_utils as utils
        profile = utils.parse_newest_profile(pprof_prefix)
        
        # Extract lock names from all samples
        lock_names = set()
        acquire_count = 0
        release_count = 0
        
        for sample in profile.sample:
            # Find sample type index for lock-acquire and lock-release
            sample_types = {st.type: idx for idx, st in enumerate(profile.sample_type)}
            
            # Get lock name label (note: it's "lock name" with a space, not "lock-name" with hyphen!)
            for label in sample.label:
                label_key_idx = label.key
                label_key = profile.string_table[label_key_idx]
                
                if label_key == "lock name":
                    lock_name_idx = label.str
                    lock_name = profile.string_table[lock_name_idx]
                    lock_names.add(lock_name)
            
            # Count acquire/release
            if "lock-acquire" in sample_types:
                idx = sample_types["lock-acquire"]
                if sample.value[idx] > 0:
                    acquire_count += 1
            
            if "lock-release" in sample_types:
                idx = sample_types["lock-release"]
                if sample.value[idx] > 0:
                    release_count += 1
        
        print(f"\nSummary:")
        print(f"  Lock acquire samples: {acquire_count}")
        print(f"  Lock release samples: {release_count}")
        print(f"  Unique lock names: {len(lock_names)}")
        
        print(f"\nLock names found in local pprof:")
        print("-" * 80)
        
        # Categorize lock names
        good_names = []
        bad_names = []
        
        if lock_names:
            for name in sorted(lock_names):
                # Good names contain variable names
                if any(var in name for var in [
                    "request_lock", "response_lock", "auth_lock", "cache_lock",
                    "database_lock", "global_database_lock"
                ]):
                    good_names.append(name)
                else:
                    bad_names.append(name)
            
            if good_names:
                print(f"\n✅ GOOD: Lock names with variable names ({len(good_names)}):")
                for name in good_names:
                    print(f"  • {name}")
            
            if bad_names:
                print(f"\n⚠️  GENERIC: Lock names without variable names ({len(bad_names)}):")
                for name in bad_names[:10]:
                    print(f"  • {name}")
                if len(bad_names) > 10:
                    print(f"  ... and {len(bad_names) - 10} more")
        else:
            print("❌ No lock names found!")
        
        print("\n" + "-" * 80)
        
        # Provide interpretation
        print("\n📊 Interpretation:")
        if good_names and len(good_names) > 0:
            print("  ✅ Lock names ARE being captured in local pprof files")
            print("  ✅ Collector is working correctly")
            print("\n  Next steps:")
            print("  1. Check if these names appear in Datadog UI")
            print("  2. If UI shows generic names, issue is in upload/backend pipeline")
            print("  3. If UI shows correct names, everything is working!")
        else:
            print("  ❌ Lock names are NOT being captured in local pprof files")
            print("  ❌ Issue is in the collector itself")
            print("\n  Next steps:")
            print("  1. Check collector configuration")
            print("  2. Verify frame inspection is working")
            print("  3. Compare with unit test environment")
        
    except Exception as e:
        print(f"❌ Error parsing pprof file: {e}")
        import traceback
        traceback.print_exc()
    
    print("\n" + "="*80)


def main():
    """Main entry point"""
    duration = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    num_workers = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    
    print("\n" + "="*80)
    print("Lock Debug Stress Test")
    print("="*80)
    print("\nThis test writes profiling data to TWO places:")
    print(f"  1. Local pprof files: {os.environ['DD_PROFILING_OUTPUT_PPROF']}")
    print("  2. Datadog backend (via agent or agentless)")
    print("\n" + "="*80)
    
    # Clean up old pprof files
    pprof_pattern = os.environ["DD_PROFILING_OUTPUT_PPROF"] + ".*"
    old_files = glob.glob(pprof_pattern)
    if old_files:
        print(f"\nCleaning up {len(old_files)} old pprof file(s)...")
        for f in old_files:
            try:
                os.remove(f)
            except Exception as e:
                print(f"  Warning: Could not remove {f}: {e}")
    
    # Run workloads
    run_workloads(duration, num_workers)
    
    # Force upload before checking files
    print("\nForcing profile upload...")
    try:
        from ddtrace.internal.datadog.profiling import ddup
        ddup.upload()  # type: ignore
        print("✓ ddup.upload() called")
    except Exception as e:
        print(f"⚠️  Warning: Could not call ddup.upload(): {e}")
    
    # Wait for profile write
    print("\nWaiting 10 seconds for profile write...")
    time.sleep(10)
    
    # Analyze local pprof
    analyze_local_pprof()
    
    # Instructions for UI
    print("\n" + "="*80)
    print("Next Steps: Check Datadog UI")
    print("="*80)
    print("\n1. Go to: https://app.datadoghq.com/profiling")
    print(f"2. Filter by service: {os.environ['DD_SERVICE']}")
    print(f"3. Filter by environment: {os.environ['DD_ENV']}")
    print("4. Look at Lock profile type")
    print("5. Click on samples to see lock names")
    print("\nExpected lock names in UI:")
    print("  • request_lock")
    print("  • response_lock")
    print("  • auth_lock")
    print("  • cache_lock")
    print("  • global_database_lock")
    print("\n" + "="*80 + "\n")


if __name__ == "__main__":
    main()

