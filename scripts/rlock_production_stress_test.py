#!/usr/bin/env python3
"""
RLock Production Stress Test

This script combines the structure of unit tests from tests/profiling_v2/collector/test_threading.py
with production profiler upload to verify lock names are correctly captured and sent to Datadog.

The goal is to confirm that lock names (variable names, class attributes, etc.) appear in the
profiling UI and not just low-level threading library lock names.

Usage:
    # Using Datadog Agent
    export DD_AGENT_HOST="localhost"
    export DD_TRACE_AGENT_PORT="8126"
    python scripts/rlock_production_stress_test.py

    # Using Agentless mode
    export DD_API_KEY="your-api-key"
    export DD_SITE="datadoghq.com"
    python scripts/rlock_production_stress_test.py
"""
import os
import sys
import threading
import time
from typing import Any, Type

# Configure profiler BEFORE importing ddtrace
os.environ["DD_PROFILING_ENABLED"] = "1"
os.environ["DD_PROFILING_LOCK_ENABLED"] = "1"
os.environ["DD_SERVICE"] = "rlock-production-stress-test"
os.environ["DD_ENV"] = "stress-test"
os.environ["DD_VERSION"] = "1.0.0"
os.environ["DD_PROFILING_CAPTURE_PCT"] = "100"

# Import profiler auto-start
import ddtrace.profiling.auto  # noqa: E402

print("\n" + "="*80)
print("RLock Production Stress Test - Lock Name Verification")
print("="*80)
print(f"Service: {os.environ['DD_SERVICE']}")
print(f"Environment: {os.environ['DD_ENV']}")
print(f"Version: {os.environ['DD_VERSION']}")
print("="*80 + "\n")

# ============================================================================
# Test Classes - Similar to unit tests
# ============================================================================

# Module-level global locks (should be detected with global variable names)
global_user_session_lock = None
global_cache_lock = None
global_database_lock = None


class UserManager:
    """Class with named member locks - similar to Foo class in unit tests"""
    def __init__(self, lock_class: Type[Any]):
        self.user_session_lock = lock_class()
        self.user_cache_lock = lock_class()
        self.login_attempts_lock = lock_class()
    
    def authenticate_user(self, user_id: str, iterations: int):
        """Method that uses named class member lock"""
        for i in range(iterations):
            with self.user_session_lock:
                # Simulate authentication work
                time.sleep(0.001)
    
    def update_cache(self, iterations: int):
        """Method that uses another named class member lock"""
        for i in range(iterations):
            with self.user_cache_lock:
                time.sleep(0.0005)
    
    def track_login_attempts(self, iterations: int):
        """Method with reentrant lock usage"""
        for i in range(iterations):
            with self.login_attempts_lock:
                # First level
                self._validate_attempt()
    
    def _validate_attempt(self):
        """Helper method that re-acquires the same RLock (reentrant)"""
        with self.login_attempts_lock:
            time.sleep(0.0005)


class CacheManager:
    """Another class to create diverse lock patterns"""
    def __init__(self, lock_class: Type[Any]):
        self.cache_read_lock = lock_class()
        self.cache_write_lock = lock_class()
        self._internal_lock = lock_class()  # Private member
    
    def read_cache(self, iterations: int):
        for i in range(iterations):
            with self.cache_read_lock:
                time.sleep(0.0003)
    
    def write_cache(self, iterations: int):
        for i in range(iterations):
            with self.cache_write_lock:
                time.sleep(0.002)  # Longer write time
    
    def internal_operation(self, iterations: int):
        """Uses private member lock"""
        for i in range(iterations):
            with self._internal_lock:
                time.sleep(0.0005)


class DatabaseConnection:
    """Nested class structure - similar to Bar class in unit tests"""
    def __init__(self, lock_class: Type[Any]):
        self.connection_pool_lock = lock_class()
        self.query_lock = lock_class()
        self.user_manager = UserManager(lock_class)
    
    def execute_query(self, iterations: int):
        """Uses own lock"""
        for i in range(iterations):
            with self.query_lock:
                time.sleep(0.001)
    
    def get_connection(self, iterations: int):
        """Uses connection pool lock with contention"""
        for i in range(iterations):
            with self.connection_pool_lock:
                time.sleep(0.002)
    
    def execute_with_user_auth(self, user_id: str, iterations: int):
        """Accesses nested object's locks"""
        for i in range(iterations):
            with self.user_manager.user_session_lock:
                time.sleep(0.001)


# ============================================================================
# Workload Functions
# ============================================================================

def test_local_named_locks(worker_id: int, iterations: int):
    """
    Test local variable locks - should show lock names like "request_lock", "response_lock"
    Similar to test_lock_events in unit tests
    """
    for i in range(iterations):
        # Named local locks
        request_lock = threading.RLock()
        response_lock = threading.RLock()
        
        with request_lock:
            time.sleep(0.0005)
        
        with response_lock:
            time.sleep(0.0005)
        
        time.sleep(0.001)


def test_global_locks(worker_id: int, iterations: int):
    """
    Test global locks - should show lock names like "global_user_session_lock"
    Similar to test_global_locks in unit tests
    """
    global global_user_session_lock, global_cache_lock, global_database_lock
    
    for i in range(iterations):
        if global_user_session_lock is not None:
            with global_user_session_lock:
                time.sleep(0.001)
        
        if global_cache_lock is not None:
            with global_cache_lock:
                time.sleep(0.001)
        
        if i % 5 == 0 and global_database_lock is not None:
            with global_database_lock:
                time.sleep(0.002)


def test_class_member_locks(worker_id: int, iterations: int):
    """
    Test class member locks - should show lock names like "user_session_lock", "cache_read_lock"
    Similar to test_class_member_lock in unit tests
    """
    user_manager = UserManager(threading.RLock)
    cache_manager = CacheManager(threading.RLock)
    
    for i in range(iterations):
        user_manager.authenticate_user(f"user-{worker_id}", 5)
        user_manager.update_cache(5)
        cache_manager.read_cache(5)
        if i % 3 == 0:
            cache_manager.write_cache(2)


def test_nested_class_locks(worker_id: int, iterations: int):
    """
    Test nested class locks - should show lock names from nested structures
    Similar to test_inner_lock in unit tests
    """
    db_connection = DatabaseConnection(threading.RLock)
    
    for i in range(iterations):
        db_connection.execute_query(5)
        db_connection.get_connection(3)
        db_connection.execute_with_user_auth(f"user-{worker_id}", 3)


def test_reentrant_locks(worker_id: int, iterations: int):
    """
    Test RLock reentrancy - should show clear reentrant acquisition patterns
    This is the key RLock feature
    """
    user_manager = UserManager(threading.RLock)
    
    for i in range(iterations):
        user_manager.track_login_attempts(10)


def test_private_member_locks(worker_id: int, iterations: int):
    """
    Test private member locks - should show mangled names like "_CacheManager__internal_lock"
    Similar to test_private_lock in unit tests
    """
    cache_manager = CacheManager(threading.RLock)
    
    for i in range(iterations):
        cache_manager.internal_operation(10)


def test_anonymous_locks(worker_id: int, iterations: int):
    """
    Test anonymous locks (no variable name) - should still work but without variable name
    Similar to test_anonymous_lock in unit tests
    """
    for i in range(iterations):
        with threading.RLock():
            time.sleep(0.0005)


def test_high_contention_named_locks(worker_id: int, iterations: int, shared_manager: UserManager):
    """
    Test high contention on named locks - should show wait times with lock names
    """
    for i in range(iterations):
        with shared_manager.user_session_lock:
            time.sleep(0.005)  # Hold lock longer to create contention


# ============================================================================
# Stress Test Orchestration
# ============================================================================

class StressTestOrchestrator:
    """Orchestrates the stress test with different phases"""
    
    def __init__(self, duration_seconds: int, num_workers: int):
        self.duration_seconds = duration_seconds
        self.num_workers = num_workers
        self.shared_manager = UserManager(threading.RLock)
        
        # Initialize global locks
        global global_user_session_lock, global_cache_lock, global_database_lock
        global_user_session_lock = threading.RLock()
        global_cache_lock = threading.RLock()
        global_database_lock = threading.RLock()
    
    def run_phase(self, phase_name: str, target_func, duration: float):
        """Run a test phase with multiple workers"""
        print(f"\n{phase_name}")
        print("-" * 80)
        
        threads = []
        start_time = time.time()
        
        # Start workers
        for worker_id in range(self.num_workers):
            t = threading.Thread(
                target=self._worker_wrapper,
                args=(worker_id, target_func, start_time + duration),
                name=f"{phase_name}-Worker-{worker_id}"
            )
            threads.append(t)
            t.start()
        
        # Wait for phase completion
        for t in threads:
            t.join()
        
        elapsed = time.time() - start_time
        print(f"✓ Phase completed in {elapsed:.2f}s")
    
    def _worker_wrapper(self, worker_id: int, target_func, end_time: float):
        """Wrapper to run target function until time expires"""
        iteration = 0
        while time.time() < end_time:
            if target_func == test_high_contention_named_locks:
                target_func(worker_id, 10, self.shared_manager)
            else:
                target_func(worker_id, 10)
            iteration += 1
    
    def run(self):
        """Run all test phases"""
        phase_duration = self.duration_seconds / 7  # 7 different phases
        
        print("\n" + "="*80)
        print("Starting Multi-Phase RLock Stress Test")
        print("="*80)
        print(f"Total Duration: {self.duration_seconds}s")
        print(f"Workers per Phase: {self.num_workers}")
        print(f"Phase Duration: {phase_duration:.1f}s each")
        print("="*80)
        
        # Phase 1: Local Named Locks
        self.run_phase("Phase 1: Local Named Locks", test_local_named_locks, phase_duration)
        
        # Phase 2: Global Locks
        self.run_phase("Phase 2: Global Named Locks", test_global_locks, phase_duration)
        
        # Phase 3: Class Member Locks
        self.run_phase("Phase 3: Class Member Locks", test_class_member_locks, phase_duration)
        
        # Phase 4: Nested Class Locks
        self.run_phase("Phase 4: Nested Class Locks", test_nested_class_locks, phase_duration)
        
        # Phase 5: Reentrant Locks
        self.run_phase("Phase 5: Reentrant Lock Patterns", test_reentrant_locks, phase_duration)
        
        # Phase 6: Private Member Locks
        self.run_phase("Phase 6: Private Member Locks", test_private_member_locks, phase_duration)
        
        # Phase 7: High Contention on Named Locks
        self.run_phase("Phase 7: High Contention (Named Locks)", test_high_contention_named_locks, phase_duration)
        
        print("\n" + "="*80)
        print("All Phases Completed!")
        print("="*80)


def print_expected_lock_names():
    """Print the lock names we expect to see in the profiling UI"""
    print("\n" + "="*80)
    print("Expected Lock Names in Profiling UI")
    print("="*80)
    print("\nYou should see lock names like:")
    print("\nLocal Locks:")
    print("  - request_lock")
    print("  - response_lock")
    print("\nGlobal Locks:")
    print("  - global_user_session_lock")
    print("  - global_cache_lock")
    print("  - global_database_lock")
    print("\nClass Member Locks:")
    print("  - user_session_lock")
    print("  - user_cache_lock")
    print("  - login_attempts_lock")
    print("  - cache_read_lock")
    print("  - cache_write_lock")
    print("  - connection_pool_lock")
    print("  - query_lock")
    print("\nPrivate Locks:")
    print("  - _internal_lock (or mangled: _CacheManager__internal_lock)")
    print("\nAnonymous Locks:")
    print("  - (no variable name, just filename:line)")
    print("\n" + "="*80)
    print("\nIf you only see low-level threading lock names like:")
    print("  - <threading.RLock object at 0x...>")
    print("  - threading.py:XXX")
    print("\nThen the lock name detection is NOT working correctly!")
    print("="*80 + "\n")


def main():
    """Main entry point"""
    # Parse command line arguments
    duration = int(sys.argv[1]) if len(sys.argv) > 1 else 120  # Default 2 minutes
    num_workers = int(sys.argv[2]) if len(sys.argv) > 2 else 8
    
    print("\n" + "="*80)
    print("RLock Production Stress Test - Lock Name Verification")
    print("="*80)
    print("\nPurpose:")
    print("  This test verifies that lock variable names are correctly captured")
    print("  and sent to Datadog production/agent, not just in unit tests.")
    print("\nTest Structure:")
    print("  - Similar to unit tests in tests/profiling_v2/collector/test_threading.py")
    print("  - Tests local, global, class member, nested, and private locks")
    print("  - Tests RLock reentrancy patterns")
    print("  - High intensity with multiple workers")
    print("\nConfiguration:")
    print(f"  Duration: {duration} seconds")
    print(f"  Workers per phase: {num_workers}")
    print(f"  Service: {os.environ['DD_SERVICE']}")
    print(f"  Environment: {os.environ['DD_ENV']}")
    
    # Check upload configuration
    if os.environ.get("DD_API_KEY"):
        print(f"\nUpload Mode: Agentless")
        print(f"  API Key: {'*' * 32}")
        print(f"  Site: {os.environ.get('DD_SITE', 'datadoghq.com')}")
    elif os.environ.get("DD_AGENT_HOST"):
        print(f"\nUpload Mode: Agent")
        print(f"  Agent Host: {os.environ.get('DD_AGENT_HOST')}")
        print(f"  Agent Port: {os.environ.get('DD_TRACE_AGENT_PORT', '8126')}")
    else:
        print("\n⚠️  WARNING: No upload configuration detected!")
        print("  Set DD_API_KEY + DD_SITE for agentless mode")
        print("  OR set DD_AGENT_HOST for agent mode")
    
    print_expected_lock_names()
    
    # Run stress test
    orchestrator = StressTestOrchestrator(duration, num_workers)
    start_time = time.time()
    orchestrator.run()
    total_time = time.time() - start_time
    
    print("\n" + "="*80)
    print("Stress Test Completed Successfully!")
    print("="*80)
    print(f"Total Duration: {total_time:.2f} seconds")
    
    # Wait for profile upload
    print("\nWaiting 15 seconds for final profile upload...")
    for i in range(15, 0, -1):
        print(f"  {i}...", end="\r")
        time.sleep(1)
    
    print("\n\n" + "="*80)
    print("✅ Test Complete - Check Profiling UI")
    print("="*80)
    print("\nView profiling data at:")
    print("  https://app.datadoghq.com/profiling")
    print(f"\nFilter by:")
    print(f"  Service: {os.environ['DD_SERVICE']}")
    print(f"  Environment: {os.environ['DD_ENV']}")
    print("\nWhat to look for:")
    print("  1. Lock acquire samples with variable names (not just threading.py:XXX)")
    print("  2. Lock release samples with variable names")
    print("  3. Lock wait time samples")
    print("  4. Reentrant lock patterns")
    print("\nIf lock names are NOT showing up, this indicates an issue with")
    print("lock name detection in production vs. unit tests.")
    print("="*80 + "\n")


if __name__ == "__main__":
    main()

