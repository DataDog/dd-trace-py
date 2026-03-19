#pragma once

#include <cstddef>
#include <cstdint>
#include <memory>
#include <random>
#include <vector>

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include "_memalloc_debug.h"
#include "_memalloc_tb.h"
#include "_pymacro.h"

/* The maximum heap sample size is the maximum value we can store in a heap_tracker_t.allocated_memory */
#define MAX_HEAP_SAMPLE_SIZE UINT32_MAX

/* --- Prepended header for sampled allocations ---
 *
 * Sampled allocations have a 16-byte header prepended:
 *   [SIGNATURE (8 bytes)] [metadata_ptr (8 bytes)] [user data ...]
 *                                                   ^-- returned to Python
 *
 * On free(), we read 16 bytes before the pointer.  If the signature matches,
 * we know the allocation was sampled and can extract the metadata pointer
 * directly — no hashmap lookup required.
 */

/* Magic value written at the start of every sampled-allocation header.
 * Chosen to be unlikely to appear in normal heap data. */
#define MEMALLOC_SIGNATURE UINT64_C(0xDD74ACE0DEAD0001)

/* Total size of the prepended header (signature + metadata pointer). */
#define MEMALLOC_HEADER_SIZE (sizeof(uint64_t) + sizeof(void*))

/* The header layout prepended to sampled allocations. */
struct memalloc_header_t
{
    uint64_t signature; /* Must equal MEMALLOC_SIGNATURE */
    void* metadata_ptr; /* Points to the traceback_t that owns this allocation */
};

/* heap_tracker_t is defined here (not in the .cpp) so that the hot-path methods
 * should_sample_no_cpython can be inlined at every call site, including cross-TU
 * callers in _memalloc.cpp.  For ~99.99% of allocations, should_sample returns
 * false immediately (counter < threshold), and inlining lets the compiler fold
 * that fast path directly into memalloc_alloc/memalloc_realloc. */
class heap_tracker_t
{
  public:
    /* Constructor - does not make any C Python API calls */
    heap_tracker_t(uint32_t sample_size_val);
    ~heap_tracker_t() = default;

    // Delete copy constructor and assignment operator
    heap_tracker_t(const heap_tracker_t&) = delete;
    heap_tracker_t& operator=(const heap_tracker_t&) = delete;

    /* Hot path: decide whether to sample an allocation of the given size.
     * Marked [[gnu::always_inline]] so the counter-increment + threshold-check
     * compiles into every caller — no cross-TU call on the 99.99% non-sampling path. */
    [[nodiscard]] [[gnu::always_inline]] inline bool should_sample_no_cpython(size_t size,
                                                                              uint64_t* allocated_memory_val);

    /* Track a sampled allocation. The traceback is linked into the intrusive list.
     * user_ptr is the pointer returned to Python (real_ptr + MEMALLOC_HEADER_SIZE).
     * The header at user_ptr - MEMALLOC_HEADER_SIZE is written with the signature
     * and metadata pointer. Must be called with the GIL held. */
    void add_sample_no_cpython(void* user_ptr, size_t alloc_size, std::unique_ptr<traceback_t> tb);

    /* Untrack a sampled allocation given the traceback_t* from its header.
     * Removes from the intrusive list and returns to pool. */
    void untrack_from_header_no_cpython(traceback_t* tb);

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

    /* Walk the intrusive list, clear all signatures in headers, and delete
     * all traceback_t objects. Used by deinit before destroying the tracker. */
    void clear_all_no_cpython();

  private:
    uint32_t next_sample_size_no_cpython(uint32_t sample_size);

    /* This function is called from heap_tracker_t::postfork_child() as part of
       the fork handler to reset the sampling state. */
    void reset_sampling_state_no_cpython();

    /* Link a traceback into the intrusive list */
    void list_link_no_cpython(traceback_t* tb);
    /* Unlink a traceback from the intrusive list */
    void list_unlink_no_cpython(traceback_t* tb);

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
    /* Bytes allocated since the last sample was collected */
    uint64_t allocated_memory;

    /* Intrusive doubly-linked list of tracked allocations (replaces hashmap).
     * allocs_head points to the first node; each node has list_next/list_prev. */
    traceback_t* allocs_head = nullptr;
    size_t allocs_count = 0;

    /* Debug guard to assert that GIL-protected critical sections are maintained
     * while accessing the profiler's state */
    memalloc_gil_debug_check_t gil_guard;

    /* Traceback pool - reduces allocation overhead. Access is always under GIL. */
    static constexpr size_t POOL_CAPACITY = 128;
    std::vector<std::unique_ptr<traceback_t>> pool;
};

/* Inline method definitions.
 * should_sample_no_cpython is on the critical per-allocation path and must be
 * inlined at every call site, including cross-TU callers that include this header.
 * The 99.99% fast path is a counter increment + single comparison. */

inline bool
heap_tracker_t::should_sample_no_cpython(size_t size, uint64_t* allocated_memory_val)
{
    memalloc_gil_debug_guard_t guard(gil_guard);
    allocated_memory += size;
    *allocated_memory_val = allocated_memory;

    /* Check if we have enough sample or not */
    if (MEMALLOC_LIKELY(allocated_memory < current_sample_size)) {
        return false;
    }

    if (MEMALLOC_UNLIKELY(allocs_count > TRACEBACK_ARRAY_MAX_COUNT)) {
        /* TODO(nick) this is vestigial from the original array-based
         * implementation. Do we actually want this? It gives us bounded memory
         * use, but the size limit is arbitrary and once we hit the arbitrary
         * limit our reported numbers will be inaccurate.
         */
        return false;
    }

    return true;
}

/* Non-inline public API */

[[nodiscard]] bool
memalloc_heap_tracker_init_no_cpython(uint32_t sample_size);
void
memalloc_heap_tracker_deinit_no_cpython(void);

void
memalloc_heap_no_cpython(void);

/* Hot-path inline wrappers.
 * Both are called on every alloc/free; inlining eliminates the cross-TU call
 * overhead for the common case (null guard + fast path). */

/* Check whether user_ptr was a sampled allocation by reading the prepended header.
 * Returns true if the signature matches.  Note: only checks the signature. */
[[gnu::always_inline]] inline bool
memalloc_heap_is_sampled(void* user_ptr)
{
    if (MEMALLOC_UNLIKELY(!user_ptr)) {
        return false;
    }
    const memalloc_header_t* header =
      reinterpret_cast<const memalloc_header_t*>(static_cast<const char*>(user_ptr) - MEMALLOC_HEADER_SIZE);
    return header->signature == MEMALLOC_SIGNATURE;
}

/* Check whether we should sample an allocation of the given size.
 * Must be called with the GIL held. Returns true if we should sample,
 * and sets allocated_memory_val to the current allocated_memory value
 * (used as the weight for the sample). */
[[gnu::always_inline]] inline bool
memalloc_heap_should_sample_no_cpython(size_t size, uint64_t* allocated_memory_val)
{
    if (MEMALLOC_UNLIKELY(!heap_tracker_t::instance)) {
        return false;
    }
    return heap_tracker_t::instance->should_sample_no_cpython(size, allocated_memory_val);
}

/* Slow path: collect a traceback and track the sampled allocation.
 * Called AFTER should_sample returned true and the allocation (with header space)
 * has been performed. user_ptr points to the user-visible region (real_ptr + 16).
 * allocated_memory_val is the weight from should_sample. */
void
memalloc_heap_track_invokes_cpython(uint16_t max_nframe,
                                    void* user_ptr,
                                    size_t size,
                                    uint64_t allocated_memory_val,
                                    PyMemAllocatorDomain domain);

/* Untrack a sampled allocation given the traceback_t* extracted from its header.
 * Removes the traceback from the intrusive list and returns it to the pool. */
void
memalloc_heap_untrack_from_header_no_cpython(void* metadata_ptr);

void
memalloc_heap_postfork_child(void);
