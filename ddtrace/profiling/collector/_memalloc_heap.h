#pragma once

#include <cstddef>
#include <cstdint>
#include <memory>
#include <random>
#include <vector>

#include <Python.h>

#include "_memalloc_debug.h"
#include "_memalloc_tb.h"
#include "_pymacro.h"

/* The maximum heap sample size is the maximum value we can store in a heap_tracker_t.allocated_memory */
#define MAX_HEAP_SAMPLE_SIZE UINT32_MAX

/* Use Abseil's flat_hash_map for tracking sampled allocations.
 * flat_hash_map provides excellent performance with low memory overhead,
 * using the Swiss Tables algorithm from Abseil.
 *
 * We use a conditional compilation to fall back to std::unordered_map
 * when Abseil is not available (e.g., in Debug builds).
 */
#if defined(NDEBUG) && !defined(DONT_COMPILE_ABSEIL)
#include "absl/container/flat_hash_map.h"
template<typename K, typename V>
using HeapMapType = absl::flat_hash_map<K, V>;
#else
#include <unordered_map>
template<typename K, typename V>
using HeapMapType = std::unordered_map<K, V>;
#endif // defined(NDEBUG) && !defined(DONT_COMPILE_ABSEIL)

class heap_tracker_t
{
  public:
    /* Constructor - does not make any C Python API calls */
    heap_tracker_t(uint32_t sample_size_val);
    ~heap_tracker_t() = default;

    // Delete copy constructor and assignment operator
    heap_tracker_t(const heap_tracker_t&) = delete;
    heap_tracker_t& operator=(const heap_tracker_t&) = delete;

    /* Remove an allocation at the given address, if we are tracking it. This
     * function accesses the heap tracker data structures. It must be called with the
     * GIL held and must not make any C Python API calls. The traceback is deleted
     * internally if found. */
    void untrack_no_cpython(void* ptr);

    /* Decide whether we should sample an allocation of the given size. Accesses
     * shared state, and must be called with the GIL held and without making any C
     * Python API calls. Returns true if we should sample, and sets allocated_memory_val
     * to the current allocated_memory value. */
    [[nodiscard]] MEMALLOC_ALWAYS_INLINE bool should_sample_no_cpython(size_t size, uint64_t* allocated_memory_val);

    /* Track an allocation that we decided to sample. This updates shared state and
     * must be called with the GIL held and without making any C Python API calls.
     * If an allocation at the same address is already tracked, the old traceback
     * is deleted internally. */
    void add_sample_no_cpython(void* ptr, std::unique_ptr<traceback_t> tb);

    void export_heap_no_cpython();

    /* Global instance of the heap tracker */
    static heap_tracker_t* instance;

    /* Traceback pool operations */
    std::unique_ptr<traceback_t> pool_get_with_alloc_data_invokes_cpython(size_t size,
                                                                          size_t weighted_size,
                                                                          uint16_t max_nframe);
    void pool_put_no_cpython(std::unique_ptr<traceback_t> tb);

    /* Reset the heap tracker state after fork in child process */
    void postfork_child();

  private:
    uint32_t next_sample_size_no_cpython(uint32_t sample_size);

    /* This function is called from heap_tracker_t::postfork_child() as part of
       the fork handler to reset the sampling state. */
    void reset_sampling_state_no_cpython();

    /* Heap profiler sampling interval */
    uint64_t sample_size;

    /* Per-instance PRNG engine used by next_sample_size_no_cpython.
     * Declared before current_sample_size so it is initialised first in the
     * constructor member-initialiser list, allowing next_sample_size_no_cpython
     * to be called safely during current_sample_size initialisation.
     * std::minstd_rand stores all state in the object (no global locks), so it
     * is fork-safe (unlike rand). */
    std::minstd_rand rng;
    /* Next heap sample target, in bytes allocated */
    uint64_t current_sample_size;
    /* Tracked allocations - using unique_ptr for automatic memory management */
    HeapMapType<void*, std::unique_ptr<traceback_t>> allocs_m;
    /* Bytes allocated since the last sample was collected */
    uint64_t allocated_memory;

    /* Debug guard to assert that GIL-protected critical sections are maintained
     * while accessing the profiler's state */
    memalloc_gil_debug_check_t gil_guard;

    /* Traceback pool - reduces allocation overhead. Access is always under GIL. */
    static constexpr size_t POOL_CAPACITY = 128;
    std::vector<std::unique_ptr<traceback_t>> pool;
};

/* Inline definition of should_sample_no_cpython.
 * On the critical per-allocation path; inlined so the common case (counter
 * increment + comparison → return false) compiles down to a few instructions
 * directly in the caller, without a cross-TU call. */

MEMALLOC_ALWAYS_INLINE bool
heap_tracker_t::should_sample_no_cpython(size_t size, uint64_t* allocated_memory_val)
{
    memalloc_gil_debug_guard_t guard(gil_guard);
    allocated_memory += size;
    *allocated_memory_val = allocated_memory;

    /* Check if we have enough sample or not */
    if (MEMALLOC_LIKELY(allocated_memory < current_sample_size)) {
        return false;
    }

    if (MEMALLOC_UNLIKELY(allocs_m.size() > TRACEBACK_ARRAY_MAX_COUNT)) {
        /* TODO(nick) this is vestigial from the original array-based
         * implementation. Do we actually want this? It gives us bounded memory
         * use, but the size limit is arbitrary and once we hit the arbitrary
         * limit our reported numbers will be inaccurate.
         */
        return false;
    }

    return true;
}

/* Public API */

[[nodiscard]] bool
memalloc_heap_tracker_init_no_cpython(uint32_t sample_size);
void
memalloc_heap_tracker_deinit_no_cpython(void);

void
memalloc_heap_no_cpython(void);

/* Slow path: called only when should_sample_no_cpython returned true (rare).
 * allocated_memory_val is the value set by should_sample_no_cpython, used as
 * the weighted sample size for the traceback. */
void
memalloc_heap_track_slow_path_invokes_cpython(uint16_t max_nframe,
                                              void* ptr,
                                              size_t size,
                                              uint64_t allocated_memory_val,
                                              PyMemAllocatorDomain domain);

/* Inline fast path for tracking an allocation.
 * For ~99.99% of allocations should_sample_no_cpython returns false and this
 * entire function compiles down to a counter increment + comparison. */
MEMALLOC_ALWAYS_INLINE void
memalloc_heap_track_invokes_cpython(uint16_t max_nframe, void* ptr, size_t size, PyMemAllocatorDomain domain)
{
    if (MEMALLOC_UNLIKELY(heap_tracker_t::instance == nullptr)) {
        return;
    }
    uint64_t allocated_memory_val = 0;
    if (MEMALLOC_LIKELY(!heap_tracker_t::instance->should_sample_no_cpython(size, &allocated_memory_val))) {
        return;
    }
    /* Slow path — reached only when a sample is due (~0.01% of allocations) */
    memalloc_heap_track_slow_path_invokes_cpython(max_nframe, ptr, size, allocated_memory_val, domain);
}

void
memalloc_heap_untrack_no_cpython(void* ptr);

void
memalloc_heap_postfork_child(void);
