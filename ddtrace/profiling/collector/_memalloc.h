#ifndef _DDTRACE_MEMALLOC_H
#define _DDTRACE_MEMALLOC_H

#include <stdint.h>

#include <Python.h>

#include "_memalloc_reentrant.h"
#include "_memalloc_tb.h"
#include "_utils.h"

#define MEMALLOC_HEAP_PTR_ARRAY_COUNT_TYPE uint64_t
#define MEMALLOC_HEAP_PTR_ARRAY_MAX_COUNT UINT64_MAX
DO_ARRAY(void*, ptr, MEMALLOC_HEAP_PTR_ARRAY_COUNT_TYPE, DO_NOTHING)

typedef struct
{
    /* Heap profiler sampling interval */
    uint64_t sample_size;
    /* Next heap sample target, in bytes allocated */
    uint64_t current_sample_size;
    /* Tracked allocations */
    traceback_array_t allocs;
    /* Bytes allocated since the last sample was collected */
    uint64_t allocated_memory;
    /* True if the heap tracker is frozen */
    bool frozen;
    /* Contains the ongoing heap allocation/deallocation while frozen */
    struct
    {
        traceback_array_t allocs;
        ptr_array_t frees;
    } freezer;
} heap_tracker_t;

typedef struct
{
    /* List of traceback */
    traceback_array_t allocs;
    /* Total number of allocations */
    uint64_t alloc_count;
} alloc_tracker_t;

typedef struct
{
    /* The original allocator we're wrapping */
    PyMemAllocatorEx pymem_allocator_obj;
    /* The domain we are tracking */
    PyMemAllocatorDomain domain;
    /* The maximum number of events for allocation tracking */
    uint16_t max_events;
    /* The maximum number of frames collected in stack traces */
    uint16_t max_nframe;

    /* active indicates we're initialized and actively tracking allocations */
    bool active;
    /* one_time_init indicates whether we've done initialization which should
       only be performed once even if the profiler is started and stopped,
       like installing fork handlers */
    bool one_time_init;

    /* This lock protects access to alloc_profile. The GIL is NOT sufficient
       to protect our data structures from concurrent access. For one, the GIL is an
       implementation detail and may go away in the future. Additionally, even if the
       GIL is held on _entry_ to our C extension functions, making it safe to call
       Python C API functions, the GIL can be released during Python C API calls if
       we call back into interpreter code. This can happen if we allocate a Python
       object (such as frame info), trigger garbage collection, and run arbitrary
       destructors. When this happens, other threads can run python code, such as the
       thread that aggregates and uploads the profile data and mutates the global
       data structures. The GIL does not create critical sections for C extension
       functions!
     */
    memlock_t alloc_lock;
    alloc_tracker_t alloc_profile;

    /* This lock protects heap_profile. See alloc_lock */
    memlock_t heap_lock;
    heap_tracker_t heap_profile;
} memalloc_context_t;

#endif
