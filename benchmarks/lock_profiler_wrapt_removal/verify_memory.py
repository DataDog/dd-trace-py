#!/usr/bin/env python3
"""
Comprehensive memory comparison: wrapt vs __slots__
Tests BOTH implementations in a single script, no branch switching needed.
"""

import sys
import gc
import threading
from typing import Any, Optional


# Mock objects for testing
class FakeSampler:
    """Mock sampler for testing."""
    def capture(self):
        return False


class FakeTracer:
    """Mock tracer for testing."""
    pass


# ============================================================================
# IMPLEMENTATION 1: wrapt-based (from main branch)
# ============================================================================

try:
    import wrapt
    
    class WraptProfiledLock(wrapt.ObjectProxy):
        """Original implementation using wrapt.ObjectProxy."""
        
        def __init__(self, wrapped, tracer, max_nframes, capture_sampler, endpoint_collection_enabled):
            super().__init__(wrapped)
            # These attributes get stored in a hidden __dict__ because
            # wrapt.ObjectProxy only defines __slots__ = ('__wrapped__',)
            self._self_tracer = tracer
            self._self_max_nframes = max_nframes
            self._self_capture_sampler = capture_sampler
            self._self_endpoint_collection_enabled = endpoint_collection_enabled
            self._self_init_loc = "test.py:42"
            self._self_acquired_at = 0
            self._self_name = None
    
    WRAPT_AVAILABLE = True
except ImportError:
    WRAPT_AVAILABLE = False
    WraptProfiledLock = None


# ============================================================================
# IMPLEMENTATION 2: __slots__-based (from unwrapt branch)  
# ============================================================================

class SlotsProfiledLock:
    """New implementation using __slots__."""
    
    __slots__ = (
        "__wrapped__",
        "_self_tracer",
        "_self_max_nframes",
        "_self_capture_sampler",
        "_self_endpoint_collection_enabled",
        "_self_init_loc",
        "_self_acquired_at",
        "_self_name",
    )
    
    def __init__(self, wrapped, tracer, max_nframes, capture_sampler, endpoint_collection_enabled):
        self.__wrapped__ = wrapped
        self._self_tracer = tracer
        self._self_max_nframes = max_nframes
        self._self_capture_sampler = capture_sampler
        self._self_endpoint_collection_enabled = endpoint_collection_enabled
        self._self_init_loc = "test.py:42"
        self._self_acquired_at = 0
        self._self_name = None


class SlotsProfiledLock7:
    """Optimized version with 7 slots (removed _self_endpoint_collection_enabled)."""
    
    __slots__ = (
        "__wrapped__",
        "_self_tracer",
        "_self_max_nframes",
        "_self_capture_sampler",
        "_self_init_loc",
        "_self_acquired_at",
        "_self_name",
    )
    
    def __init__(self, wrapped, tracer, max_nframes, capture_sampler):
        self.__wrapped__ = wrapped
        self._self_tracer = tracer
        self._self_max_nframes = max_nframes
        self._self_capture_sampler = capture_sampler
        self._self_init_loc = "test.py:42"
        self._self_acquired_at = 0
        self._self_name = None


# ============================================================================
# MEASUREMENT FUNCTIONS
# ============================================================================

def measure_memory(obj, name="object"):
    """
    Measure total memory including hidden structures.
    
    Returns: (visible_size, hidden_dict_size, total_size)
    """
    visible = sys.getsizeof(obj)
    hidden_dict = 0
    
    # Find hidden __dict__ using gc.get_referents
    refs = gc.get_referents(obj)
    for ref in refs:
        if isinstance(ref, dict):
            # Check if this is OUR dict (contains _self_ attributes)
            if any(k.startswith('_self_') for k in ref.keys() if isinstance(k, str)):
                hidden_dict = sys.getsizeof(ref)
                print(f"     Found hidden __dict__: {hidden_dict} bytes")
                print(f"       Keys: {list(ref.keys())}")
                break
    
    total = visible + hidden_dict
    
    return visible, hidden_dict, total


def test_implementation(cls, name, *args):
    """Test a single implementation and return measurements."""
    print(f"\n{'='*70}")
    print(f"{name}")
    print(f"{'='*70}")
    
    # Create instance
    lock = threading.Lock()
    try:
        instance = cls(lock, *args)
    except TypeError as e:
        print(f"  ❌ Failed to create: {e}")
        return None
    
    # Measure
    visible, hidden, total = measure_memory(instance, name)
    
    print(f"  Visible size (sys.getsizeof):     {visible:>4} bytes")
    print(f"  Hidden __dict__ size:             {hidden:>4} bytes")
    print(f"  {'─'*60}")
    print(f"  TOTAL:                            {total:>4} bytes")
    
    # Check for __slots__
    has_slots = hasattr(cls, '__slots__')
    if has_slots:
        slot_count = len(cls.__slots__)
        print(f"\n  Uses __slots__: YES ({slot_count} slots)")
        print(f"    Slots: {cls.__slots__}")
    else:
        print(f"\n  Uses __slots__: NO")
    
    return {
        'name': name,
        'visible': visible,
        'hidden_dict': hidden,
        'total': total,
        'has_slots': has_slots,
        'slot_count': slot_count if has_slots else 0
    }


# ============================================================================
# MAIN TEST
# ============================================================================

def main():
    print("="*70)
    print("EMPIRICAL MEMORY COMPARISON: wrapt vs __slots__")
    print("="*70)
    print("\nTesting all implementations with identical configuration:")
    print("  - tracer: None")
    print("  - max_nframes: 64")
    print("  - capture_sampler: FakeSampler()")
    print("  - endpoint_collection_enabled: True (where applicable)")
    
    sampler = FakeSampler()
    results = []
    
    # Test wrapt implementation
    if WRAPT_AVAILABLE:
        result = test_implementation(
            WraptProfiledLock,
            "1. WRAPT IMPLEMENTATION (main branch)",
            None,  # tracer
            64,    # max_nframes
            sampler,
            True   # endpoint_collection_enabled
        )
        if result:
            results.append(result)
    else:
        print("\n⚠️  wrapt not available - skipping wrapt tests")
    
    # Test __slots__ with 8 slots
    result = test_implementation(
        SlotsProfiledLock,
        "2. __SLOTS__ IMPLEMENTATION - 8 slots (unwrapt branch original)",
        None,
        64,
        sampler,
        True
    )
    if result:
        results.append(result)
    
    # Test __slots__ with 7 slots (optimized)
    result = test_implementation(
        SlotsProfiledLock7,
        "3. __SLOTS__ IMPLEMENTATION - 7 slots (optimized)",
        None,
        64,
        sampler
    )
    if result:
        results.append(result)
    
    # Summary
    if len(results) >= 2:
        print(f"\n{'='*70}")
        print("SUMMARY")
        print(f"{'='*70}")
        
        for i, result in enumerate(results, 1):
            print(f"\n{i}. {result['name']}")
            print(f"   Total: {result['total']} bytes", end="")
            if result['has_slots']:
                print(f" ({result['slot_count']} slots)")
            else:
                print(" (no __slots__)")
        
        # Calculate improvements
        if WRAPT_AVAILABLE and len(results) >= 2:
            wrapt_total = results[0]['total']
            slots8_total = results[1]['total']
            
            print(f"\n{'─'*70}")
            print("\nIMPROVEMENTS:")
            
            for i in range(1, len(results)):
                savings = wrapt_total - results[i]['total']
                pct = 100 * savings / wrapt_total
                print(f"\n  {results[i]['name']}:")
                print(f"    Saves: {savings} bytes ({pct:.1f}% reduction)")
            
            # Scaling examples
            print(f"\n{'─'*70}")
            print("\nMEMORY SAVINGS AT SCALE:")
            print(f"  (comparing wrapt {wrapt_total}B vs optimized {results[-1]['total']}B)")
            
            diff = wrapt_total - results[-1]['total']
            for count in [100, 1_000, 10_000, 100_000]:
                total_kb = diff * count / 1024
                if total_kb < 1024:
                    print(f"  {count:>7,} locks: saves {total_kb:>8.1f} KB")
                else:
                    print(f"  {count:>7,} locks: saves {total_kb/1024:>8.1f} MB")
    
    print(f"\n{'='*70}")
    print("✅ EMPIRICAL MEASUREMENT COMPLETE")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()

