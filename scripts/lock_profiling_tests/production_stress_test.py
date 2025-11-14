#!/usr/bin/env python3
"""
Lock/RLock Production Stress Test (Unified)

This script tests both threading.Lock() and threading.RLock() with production profiler
upload to verify lock names are correctly captured and sent to Datadog.

Usage:
    # Test RLock (default)
    export DD_AGENT_HOST="localhost"
    python scripts/lock_profiling_tests/production_stress_test.py

    # Test Lock
    python scripts/lock_profiling_tests/production_stress_test.py --lock-type Lock

    # Test both
    python scripts/lock_profiling_tests/production_stress_test.py --lock-type both

    # Using Agentless mode
    export DD_API_KEY="your-api-key"
    export DD_SITE="datadoghq.com"
    python scripts/lock_profiling_tests/production_stress_test.py
"""
import argparse
import os
import sys
import threading
import time
from typing import Any, Type


def run_stress_test(lock_class: Type[Any], lock_type_name: str):
    """Run the stress test for a given lock class"""
    
    # ============================================================================
    # Test Classes - Similar to unit tests
    # ============================================================================
    
    # Module-level global locks
    global_user_session_lock = None
    global_cache_lock = None
    global_database_lock = None
    
    
    class UserManager:
        """Class with named member locks"""
        def __init__(self, lock_cls: Type[Any]):
            self.user_session_lock = lock_cls()
            self.user_cache_lock = lock_cls()
            self.login_attempts_lock = lock_cls()
        
        def authenticate_user(self, user_id: str, iterations: int):
            """Method that uses named class member lock"""
            for i in range(iterations):
                with self.user_session_lock:
                    time.sleep(0.001)
        
        def update_cache(self, iterations: int):
            """Method that uses another named class member lock"""
            for i in range(iterations):
                with self.user_cache_lock:
                    time.sleep(0.0005)
        
        def track_login_attempts(self, iterations: int):
            """Method with lock usage"""
            for i in range(iterations):
                with self.login_attempts_lock:
                    # For RLock, can re-acquire; for Lock, cannot
                    if isinstance(self.login_attempts_lock, threading.RLock):
                        self._validate_attempt()
                    else:
                        time.sleep(0.0005)
        
        def _validate_attempt(self):
            """Helper method that re-acquires the same RLock (reentrant)"""
            with self.login_attempts_lock:
                time.sleep(0.0005)
    
    
    class CacheManager:
        """Another class to create diverse lock patterns"""
        def __init__(self, lock_cls: Type[Any]):
            self.cache_read_lock = lock_cls()
            self.cache_write_lock = lock_cls()
            self._internal_lock = lock_cls()  # Private member
        
        def read_cache(self, iterations: int):
            for i in range(iterations):
                with self.cache_read_lock:
                    time.sleep(0.0003)
        
        def write_cache(self, iterations: int):
            for i in range(iterations):
                with self.cache_write_lock:
                    time.sleep(0.002)
        
        def internal_operation(self, iterations: int):
            """Uses private member lock"""
            for i in range(iterations):
                with self._internal_lock:
                    time.sleep(0.0002)
    
    
    class APIHandler:
        """API request handler with request/response locks"""
        def __init__(self, lock_cls: Type[Any]):
            self.request_lock = lock_cls()
            self.response_lock = lock_cls()
        
        def handle_request(self, iterations: int):
            for i in range(iterations):
                with self.request_lock:
                    time.sleep(0.0008)
                with self.response_lock:
                    time.sleep(0.0006)
    
    
    class NestedLockClass:
        """Test nested class lock detection"""
        def __init__(self, lock_cls: Type[Any]):
            self.nested_inner_lock = lock_cls()
        
        def nested_operation(self, iterations: int):
            for i in range(iterations):
                with self.nested_inner_lock:
                    time.sleep(0.0001)
    
    
    # ============================================================================
    # Workload Functions
    # ============================================================================
    
    def run_phase(phase_num: int, description: str, workload_func, *args):
        """Run a single test phase"""
        print(f"\n[Phase {phase_num}] {description}")
        print(f"  Starting workload...")
        start_time = time.time()
        
        workload_func(*args)
        
        elapsed = time.time() - start_time
        print(f"  ✓ Completed in {elapsed:.2f}s")
    
    
    def workload_class_members(lock_cls: Type[Any]):
        """Workload: Class member locks"""
        manager = UserManager(lock_cls)
        cache = CacheManager(lock_cls)
        
        threads = []
        for i in range(4):
            t1 = threading.Thread(target=manager.authenticate_user, args=(f"user{i}", 100))
            t2 = threading.Thread(target=manager.update_cache, args=(150,))
            t3 = threading.Thread(target=cache.read_cache, args=(200,))
            t4 = threading.Thread(target=cache.write_cache, args=(50,))
            threads.extend([t1, t2, t3, t4])
        
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    
    
    def workload_global_locks(lock_cls: Type[Any]):
        """Workload: Global module-level locks"""
        global global_user_session_lock, global_cache_lock, global_database_lock
        
        global_user_session_lock = lock_cls()
        global_cache_lock = lock_cls()
        global_database_lock = lock_cls()
        
        def use_global_locks():
            for _ in range(100):
                with global_user_session_lock:
                    time.sleep(0.0005)
                with global_cache_lock:
                    time.sleep(0.0003)
                with global_database_lock:
                    time.sleep(0.0002)
        
        threads = [threading.Thread(target=use_global_locks) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    
    
    def workload_local_locks(lock_cls: Type[Any]):
        """Workload: Local function locks"""
        def worker():
            request_lock = lock_cls()
            response_lock = lock_cls()
            processing_lock = lock_cls()
            
            for _ in range(150):
                with request_lock:
                    time.sleep(0.0003)
                with response_lock:
                    time.sleep(0.0002)
                with processing_lock:
                    time.sleep(0.0001)
        
        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    
    
    def workload_mixed_patterns(lock_cls: Type[Any]):
        """Workload: Mixed lock patterns"""
        api = APIHandler(lock_cls)
        nested = NestedLockClass(lock_cls)
        
        threads = []
        for i in range(3):
            t1 = threading.Thread(target=api.handle_request, args=(100,))
            t2 = threading.Thread(target=nested.nested_operation, args=(200,))
            threads.extend([t1, t2])
        
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    
    
    def workload_high_contention(lock_cls: Type[Any]):
        """Workload: High contention on few locks"""
        shared_lock_1 = lock_cls()
        shared_lock_2 = lock_cls()
        
        def contention_worker():
            for _ in range(200):
                with shared_lock_1:
                    time.sleep(0.0002)
                with shared_lock_2:
                    time.sleep(0.0001)
        
        threads = [threading.Thread(target=contention_worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    
    
    def workload_private_members(lock_cls: Type[Any]):
        """Workload: Private member locks"""
        cache = CacheManager(lock_cls)
        
        threads = [threading.Thread(target=cache.internal_operation, args=(150,)) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    
    
    def workload_reentrant_patterns(lock_cls: Type[Any]):
        """Workload: Reentrant patterns (RLock specific)"""
        if lock_cls != threading.RLock:
            print("  ⊘ Skipped (requires RLock)")
            return
        
        manager = UserManager(lock_cls)
        threads = [threading.Thread(target=manager.track_login_attempts, args=(100,)) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    
    
    # ============================================================================
    # Run All Phases
    # ============================================================================
    
    print(f"\n{'='*80}")
    print(f"{lock_type_name} Production Stress Test - Lock Name Verification")
    print(f"{'='*80}")
    print(f"Service: {os.environ.get('DD_SERVICE', 'N/A')}")
    print(f"Lock Type: {lock_type_name}")
    print(f"{'='*80}")
    
    start_time = time.time()
    
    run_phase(1, "Class Member Locks", workload_class_members, lock_class)
    run_phase(2, "Global Module Locks", workload_global_locks, lock_class)
    run_phase(3, "Local Function Locks", workload_local_locks, lock_class)
    run_phase(4, "Mixed Lock Patterns", workload_mixed_patterns, lock_class)
    run_phase(5, "High Contention", workload_high_contention, lock_class)
    run_phase(6, "Private Member Locks", workload_private_members, lock_class)
    
    if lock_class == threading.RLock:
        run_phase(7, "Reentrant Patterns (RLock)", workload_reentrant_patterns, lock_class)
    
    total_time = time.time() - start_time
    
    print(f"\n{'='*80}")
    print(f"✓ All phases completed in {total_time:.2f}s")
    print(f"{'='*80}")
    print(f"\nView results at: https://app.datadoghq.com/profiling")
    print(f"  - Service: {os.environ.get('DD_SERVICE', 'N/A')}")
    print(f"  - Profile type: Lock")
    print(f"  - Look for lock names like: user_session_lock, cache_read_lock, etc.")
    print(f"{'='*80}\n")


def main():
    parser = argparse.ArgumentParser(description="Unified Lock/RLock production stress test")
    parser.add_argument(
        "--lock-type",
        choices=["Lock", "RLock", "both"],
        default="RLock",
        help="Lock type to test (default: RLock)"
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=120,
        help="Test duration in seconds (default: 120)"
    )
    args = parser.parse_args()
    
    # Configure profiler BEFORE importing ddtrace
    os.environ["DD_PROFILING_ENABLED"] = "1"
    os.environ["DD_PROFILING_LOCK_ENABLED"] = "1"
    
    if args.lock_type == "both":
        os.environ["DD_SERVICE"] = "lock-rlock-production-stress-test"
    else:
        os.environ["DD_SERVICE"] = f"{args.lock_type.lower()}-production-stress-test"
    
    os.environ["DD_ENV"] = "stress-test"
    os.environ["DD_VERSION"] = "1.0.0"
    os.environ["DD_PROFILING_CAPTURE_PCT"] = "100"
    
    # Import profiler auto-start
    import ddtrace.profiling.auto  # noqa: F401
    
    # Run tests
    if args.lock_type == "both":
        print("\n" + "="*80)
        print("Testing BOTH Lock and RLock")
        print("="*80 + "\n")
        
        print("\n" + "▼"*80)
        print("PART 1: Testing threading.Lock()")
        print("▼"*80)
        run_stress_test(threading.Lock, "Lock")
        
        print("\n" + "▼"*80)
        print("PART 2: Testing threading.RLock()")
        print("▼"*80)
        run_stress_test(threading.RLock, "RLock")
    else:
        lock_class = threading.RLock if args.lock_type == "RLock" else threading.Lock
        run_stress_test(lock_class, args.lock_type)


if __name__ == "__main__":
    main()

