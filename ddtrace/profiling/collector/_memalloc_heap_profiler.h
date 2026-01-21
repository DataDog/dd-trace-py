#pragma once

#include <cstdint>
#include <memory>
#include <vector>

#include "_memalloc_heap_sample.h"

/* The maximum number of traceback samples we can store in the heap profiler */
#define TRACEBACK_ARRAY_MAX_COUNT UINT16_MAX

/* Use Abseil's flat_hash_map for tracking sampled allocations when available */
#if defined(NDEBUG) && !defined(DONT_COMPILE_ABSEIL)
#include "absl/container/flat_hash_map.h"
template<typename K, typename V>
using HeapMapType = absl::flat_hash_map<K, V>;
#else
#include <unordered_map>
template<typename K, typename V>
using HeapMapType = std::unordered_map<K, V>;
#endif

class heap_profiler_t
{
  public:
    /* Constructor */
    heap_profiler_t(uint32_t sample_interval_val);
    ~heap_profiler_t() = default;

    // Delete copy constructor and assignment operator
    heap_profiler_t(const heap_profiler_t&) = delete;
    heap_profiler_t& operator=(const heap_profiler_t&) = delete;

    /* Remove an allocation at the given address, if we are tracking it.
     * Exports heap=0 event and removes from tracking map.
     * Must be called with GIL held and must not make any C Python API calls. */
    void untrack_no_cpython(void* ptr);

    /* Decide whether we should sample an allocation of the given size for heap profiling.
     * Must be called with the GIL held and without making any C Python API calls.
     * Returns true if we should sample. */
    bool should_sample_no_cpython(size_t size);

    /* Track an allocation for heap profiling.
     * Creates a heap sample, exports it, and adds to tracking map.
     * Must be called with the GIL held and without making any C Python API calls.
     * NOTE: Invokes CPython APIs during sample creation. */
    void track_allocation_invokes_cpython(void* ptr, size_t size, uint16_t max_nframe);

    /* Export all currently tracked heap allocations.
     * Called periodically to report current heap state. */
    void export_heap_no_cpython();

    /* Global instance of the heap profiler */
    static heap_profiler_t* instance;

    /* Heap sample pool operations */
    std::unique_ptr<heap_sample_t> pool_get_invokes_cpython(size_t size, uint16_t max_nframe);
    void pool_put_no_cpython(std::unique_ptr<heap_sample_t> sample);

  private:
    static uint32_t next_sample_interval_no_cpython(uint32_t sample_interval);

    /* Heap profiler sampling interval */
    uint64_t sample_interval;
    /* Next heap sample target, in bytes allocated */
    uint64_t current_sample_interval;
    /* Tracked heap allocations - using unique_ptr for automatic memory management */
    HeapMapType<void*, std::unique_ptr<heap_sample_t>> live_allocs;
    /* Bytes allocated since the last sample was collected */
    uint64_t allocated_memory;

    /* Sample pool - reduces allocation overhead */
    static constexpr size_t POOL_CAPACITY = 128;
    std::vector<std::unique_ptr<heap_sample_t>> pool;
};

/* Public API */
bool
memalloc_heap_profiler_init_no_cpython(uint32_t sample_interval);

void
memalloc_heap_profiler_deinit_no_cpython(void);

void
memalloc_heap_untrack_no_cpython(void* ptr);

void
memalloc_heap_export_no_cpython(void);
