#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include <Python.h>

/* The maximum heap sample size is the maximum value we can store in a heap_tracker_t.allocated_memory */
#define MAX_HEAP_SAMPLE_SIZE UINT32_MAX

[[nodiscard]] bool
memalloc_heap_tracker_init_no_cpython(uint32_t sample_size, size_t code_cache_capacity);
void
memalloc_heap_tracker_deinit_no_cpython(void);

void
memalloc_heap_no_cpython(void);

/* Sampling counter pair exposed for the inline fast-path. */
struct memalloc_sampling_counter_t
{
    uint64_t allocated_memory;
    uint64_t current_sample_size;
};

/* Points to the active heap tracker's counter.
 * NULL when the tracker is not active. */
extern memalloc_sampling_counter_t* g_memalloc_sampling_counter;

/* Slow path: called only when the byte counter crosses the threshold (~1/1000).
 * Performs the live-allocation cap check and returns true if we should sample. */
bool
memalloc_heap_sample_slow_path(uint64_t* allocated_memory_val);

/* Inline fast-path: bumps the byte counter and returns true only when the
 * threshold is crossed AND the slow-path cap check passes.
 * ~999/1000 calls return false without crossing a function-call boundary. */
static inline bool
memalloc_heap_sample_check_inline(size_t size, uint64_t* allocated_memory_val)
{
    memalloc_sampling_counter_t* counter = g_memalloc_sampling_counter;
    if (!counter) {
        return false;
    }
    counter->allocated_memory += size;
    *allocated_memory_val = counter->allocated_memory;
    if (counter->allocated_memory < counter->current_sample_size) {
        return false;
    }
    return memalloc_heap_sample_slow_path(allocated_memory_val);
}

/* Expensive path: collect traceback, record sample. Only call after
 * memalloc_heap_sample_check_inline returned true. */
void
memalloc_heap_track_sample_invokes_cpython(uint16_t max_nframe,
                                           void* ptr,
                                           size_t size,
                                           PyMemAllocatorDomain domain,
                                           uint64_t allocated_memory_val);

void
memalloc_heap_untrack_no_cpython(void* ptr);

void
memalloc_heap_postfork_child(void);
