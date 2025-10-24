#!/usr/bin/env python3
"""
Quick diagnostic to check if profiler is working
"""
import os
import sys

# Set up environment
os.environ["DD_PROFILING_ENABLED"] = "1"
os.environ["DD_PROFILING_LOCK_ENABLED"] = "1"
os.environ["DD_PROFILING_CAPTURE_PCT"] = "100"
os.environ["DD_SERVICE"] = "profiler-diagnostic"
os.environ["DD_PROFILING_OUTPUT_PPROF"] = "/tmp/profiler_diagnostic_test"

print("="*80)
print("Profiler Diagnostic Check")
print("="*80)

# Check 1: Is ddup available?
print("\n1. Checking ddup availability...")
try:
    from ddtrace.internal.datadog.profiling import ddup
    print(f"   ✓ ddup module imported")
    print(f"   ✓ ddup.is_available: {ddup.is_available}")
    if not ddup.is_available:
        print("   ✗ ERROR: ddup is not available!")
        print("   This means native profiling extensions are not compiled.")
        sys.exit(1)
except Exception as e:
    print(f"   ✗ ERROR importing ddup: {e}")
    sys.exit(1)

# Check 2: Can we import ddtrace.profiling.auto?
print("\n2. Checking profiler auto-start...")
try:
    import ddtrace.profiling.auto
    print("   ✓ ddtrace.profiling.auto imported")
except Exception as e:
    print(f"   ✗ ERROR: {e}")
    sys.exit(1)

# Check 3: Is profiler actually running?
print("\n3. Checking profiler status...")
try:
    from ddtrace.profiling import profiler as profiler_module
    
    # Try to access the global profiler instance
    if hasattr(profiler_module, '_profiler') and profiler_module._profiler is not None:
        prof = profiler_module._profiler
        print(f"   ✓ Profiler instance exists")
        print(f"   ✓ Profiler status: {prof.status}")
        
        # Check collectors
        if hasattr(prof, '_collectors'):
            print(f"   ✓ Number of collectors: {len(prof._collectors)}")
            for collector in prof._collectors:
                print(f"      - {collector.__class__.__name__}")
        
    else:
        print("   ⚠  Warning: Could not access profiler instance")
except Exception as e:
    print(f"   ⚠  Warning: {e}")

# Check 4: Test lock profiling
print("\n4. Testing lock profiling...")
import threading
import time

test_lock = threading.Lock()
print("   - Created test lock")

with test_lock:
    print("   - Acquired and released test lock")
    time.sleep(0.01)

print("   ✓ Lock operations completed")

# Check 5: Can we trigger ddup upload?
print("\n5. Attempting ddup upload...")
try:
    ddup.upload()
    print("   ✓ ddup.upload() called")
except Exception as e:
    print(f"   ⚠  Warning: {e}")

# Check 6: Look for pprof files
print("\n6. Checking for pprof files...")
import glob
pprof_pattern = os.environ["DD_PROFILING_OUTPUT_PPROF"] + ".*"
files = glob.glob(pprof_pattern)
if files:
    print(f"   ✓ Found {len(files)} pprof file(s):")
    for f in files:
        print(f"      {f}")
else:
    print(f"   ✗ No pprof files found matching: {pprof_pattern}")

# Check 7: Environment check
print("\n7. Environment variables:")
env_vars = [
    "DD_PROFILING_ENABLED",
    "DD_PROFILING_LOCK_ENABLED", 
    "DD_PROFILING_CAPTURE_PCT",
    "DD_PROFILING_OUTPUT_PPROF",
    "DD_AGENT_HOST",
    "DD_TRACE_AGENT_PORT",
    "DD_API_KEY",
    "DD_SITE",
]
for var in env_vars:
    val = os.environ.get(var)
    if var == "DD_API_KEY" and val:
        val = "*" * len(val)
    print(f"   {var}: {val if val else '(not set)'}")

print("\n" + "="*80)
print("Diagnostic Complete")
print("="*80)

if not files:
    print("\n⚠️  ISSUE: No pprof files were created!")
    print("\nPossible reasons:")
    print("1. Profiler not starting properly")
    print("2. Not enough time passed for profile to be written")
    print("3. DD_PROFILING_OUTPUT_PPROF not being respected")
    print("\nTry:")
    print("  - Wait longer (profiles written every 60s by default)")
    print("  - Check if profiler is actually starting")
    print("  - Run with more activity to generate samples")
else:
    print("\n✓ Profiler appears to be working!")
    print("\nNext steps:")
    print("  1. Parse the pprof file to check for lock names")
    print("  2. Check if data appears in Datadog UI")

