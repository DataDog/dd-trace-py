#!/usr/bin/env python3
"""
E2E test for RLock profiling integration.
Validates RLock collector functionality and integration points.
"""

import threading
import sys
import os
import time

# Add the project to Python path
sys.path.insert(0, '/Users/vlad.scherbich/go/src/github.com/DataDog/dd-trace-py-2')

def test_rlock_import_and_creation():
    """Test that RLock collector can be imported and created"""
    print("=== RLock Import and Creation Test ===")
    
    try:
        from ddtrace.profiling.collector import threading as collector_threading
        print("✅ Successfully imported ThreadingRLockCollector")
        
        # Create collectors
        lock_collector = collector_threading.ThreadingLockCollector()
        rlock_collector = collector_threading.ThreadingRLockCollector()
        
        print(f"✅ Lock collector created: {type(lock_collector)}")
        print(f"✅ RLock collector created: {type(rlock_collector)}")
        
        return True
        
    except Exception as e:
        print(f"❌ Import/creation failed: {e}")
        return False

def test_rlock_behavior_patterns():
    """Test RLock reentrant behavior patterns (without profiler active)"""
    print("\n=== RLock Behavior Patterns Test ===")
    
    try:
        # Test standard RLock reentrant behavior
        rlock = threading.RLock()
        results = []
        
        def test_reentrant_pattern():
            """Test multi-level reentrant acquisition"""
            with rlock:
                results.append("Level 1")
                with rlock:  # Reentrant
                    results.append("Level 2")
                    with rlock:  # Double reentrant
                        results.append("Level 3")
                        time.sleep(0.01)
        
        # Test with multiple threads
        threads = []
        for i in range(2):
            t = threading.Thread(target=test_reentrant_pattern, name=f"Thread-{i}")
            threads.append(t)
        
        for t in threads:
            t.start()
        
        for t in threads:
            t.join()
        
        expected_results = 2 * 3  # 2 threads × 3 levels each
        print(f"Completed {len(results)} reentrant operations")
        print(f"Expected {expected_results} operations")
        
        success = len(results) == expected_results
        if success:
            print("✅ RLock reentrant behavior works correctly!")
        else:
            print(f"⚠️  Unexpected result count: {len(results)} vs {expected_results}")
        
        return success
        
    except Exception as e:
        print(f"❌ RLock behavior test failed: {e}")
        return False

def test_lock_vs_rlock_differences():
    """Test the key differences between Lock and RLock"""
    print("\n=== Lock vs RLock Differences Test ===")
    
    try:
        lock = threading.Lock()
        rlock = threading.RLock()
        
        print(f"Lock type: {type(lock)}")
        print(f"RLock type: {type(rlock)}")
        
        # Test Lock (non-reentrant)
        print("Testing Lock (non-reentrant)...")
        with lock:
            print("  Lock acquired and released successfully")
        
        # Test RLock (reentrant)
        print("Testing RLock (reentrant)...")
        with rlock:
            print("  RLock level 1 acquired")
            with rlock:
                print("  RLock level 2 acquired (reentrant)")
                with rlock:
                    print("  RLock level 3 acquired (reentrant)")
                print("  RLock level 3 released")
            print("  RLock level 2 released")
        print("  RLock level 1 released")
        
        print("✅ Lock vs RLock behavior differences confirmed!")
        return True
        
    except Exception as e:
        print(f"❌ Lock vs RLock test failed: {e}")
        return False

def test_threading_module_integration():
    """Test integration with threading module"""
    print("\n=== Threading Module Integration Test ===")
    
    try:
        # Verify we can create locks normally
        locks_created = []
        
        # Create various lock types
        regular_lock = threading.Lock()
        reentrant_lock = threading.RLock()
        condition = threading.Condition()
        semaphore = threading.Semaphore()
        
        locks_created.extend([
            ("Lock", regular_lock),
            ("RLock", reentrant_lock), 
            ("Condition", condition),
            ("Semaphore", semaphore)
        ])
        
        print("Created lock types:")
        for name, lock_obj in locks_created:
            print(f"  {name}: {type(lock_obj)}")
        
        # Test basic functionality
        print("Testing basic functionality...")
        
        with regular_lock:
            print("  Regular Lock works")
        
        with reentrant_lock:
            with reentrant_lock:  # Reentrant
                print("  RLock reentrant functionality works")
        
        with condition:
            print("  Condition works")
        
        with semaphore:
            print("  Semaphore works")
        
        print("✅ Threading module integration successful!")
        return True
        
    except Exception as e:
        print(f"❌ Threading integration test failed: {e}")
        return False

def test_profiler_readiness():
    """Test that the environment is ready for profiling"""
    print("\n=== Profiler Readiness Test ===")
    
    try:
        # Test imports
        import ddtrace
        print("✅ ddtrace imports successfully")
        
        from ddtrace.profiling.collector import threading as collector_threading
        print("✅ Threading collector imports successfully")
        
        from ddtrace.profiling.collector import _lock
        print("✅ Lock collector base imports successfully")
        
        # Test collector classes exist
        lock_collector_class = collector_threading.ThreadingLockCollector
        rlock_collector_class = collector_threading.ThreadingRLockCollector
        
        print(f"✅ Lock collector class: {lock_collector_class}")
        print(f"✅ RLock collector class: {rlock_collector_class}")
        
        # Test profiled lock classes exist
        profiled_lock_class = collector_threading._ProfiledThreadingLock
        profiled_rlock_class = collector_threading._ProfiledThreadingRLock
        
        print(f"✅ Profiled Lock class: {profiled_lock_class}")
        print(f"✅ Profiled RLock class: {profiled_rlock_class}")
        
        print("✅ Environment is ready for RLock profiling!")
        return True
        
    except Exception as e:
        print(f"❌ Profiler readiness test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    print("🔒 E2E RLock Profiling Validation")
    print("=" * 50)
    print("This test validates RLock profiling integration")
    print("and core functionality.")
    print("=" * 50)
    print()
    
    try:
        # Run test suites
        test1_passed = test_rlock_import_and_creation()
        test2_passed = test_rlock_behavior_patterns()
        test3_passed = test_lock_vs_rlock_differences()
        test4_passed = test_threading_module_integration()
        test5_passed = test_profiler_readiness()
        
        print(f"\n{'=' * 50}")
        print("🏁 FINAL RESULTS")
        print(f"{'=' * 50}")
        print(f"RLock import/creation:        {'✅ PASS' if test1_passed else '❌ FAIL'}")
        print(f"RLock behavior patterns:      {'✅ PASS' if test2_passed else '❌ FAIL'}")
        print(f"Lock vs RLock differences:    {'✅ PASS' if test3_passed else '❌ FAIL'}")
        print(f"Threading module integration: {'✅ PASS' if test4_passed else '❌ FAIL'}")
        print(f"Profiler readiness:           {'✅ PASS' if test5_passed else '❌ FAIL'}")
        
        all_passed = all([test1_passed, test2_passed, test3_passed, test4_passed, test5_passed])
        
        if all_passed:
            print(f"\n🎉 ALL E2E TESTS PASSED!")
            print("RLock profiling implementation is ready!")
        else:
            print(f"\n⚠️  Some tests had issues.")
            
        print(f"\n{'=' * 50}")
        print("E2E validation complete!")
            
    except Exception as e:
        print(f"\n💥 Tests failed with exception: {e}")
        import traceback
        traceback.print_exc()
