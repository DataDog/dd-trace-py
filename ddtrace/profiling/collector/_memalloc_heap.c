#include <math.h>
#include <stdlib.h>

#define PY_SSIZE_T_CLEAN
#include "_memalloc_debug.h"
#include "_memalloc_heap.h"
#include "_memalloc_heap_map.h"
#include "_memalloc_reentrant.h"
#include "_memalloc_tb.h"

typedef struct
{
    /* Granularity of the heap profiler in bytes */
    uint64_t sample_size;
    /* Current sample size of the heap profiler in bytes */
    uint64_t current_sample_size;
    /* Tracked allocations */
    memalloc_heap_map_t* allocs_m;
    /* Bytes allocated since the last sample was collected */
    uint64_t allocated_memory;
    /* True if the heap tracker is frozen */
    bool frozen;
    /* Contains the ongoing heap allocation/deallocation while frozen */
    struct
    {
        memalloc_heap_map_t* allocs_m;
        ptr_array_t frees;
    } freezer;
} heap_tracker_t;

static char g_crash_on_mutex_pass_str[] = "_DD_PROFILING_MEMHEAP_CRASH_ON_MUTEX_PASS";
static memlock_t g_memheap_lock;

static heap_tracker_t global_heap_tracker;

// This is a multiplatform way to define an operation to happen at static initialization time
static void
memheap_init(void);

static void
memheap_prefork(void)
{
    // See memalloc_prefork for an explanation of why this is here
    memlock_lock(&g_memheap_lock);
}

static void
memheap_postfork_parent(void)
{
    memlock_unlock(&g_memheap_lock);
}

static void
memheap_postfork_child(void)
{
    memlock_unlock(&g_memheap_lock);
}

#ifdef _MSC_VER
#pragma section(".CRT$XCU", read)
__declspec(allocate(".CRT$XCU")) void (*memheap_init_func)(void) = memheap_init;

#elif defined(__GNUC__) || defined(__clang__)
__attribute__((constructor))
#else
#error Unsupported compiler
#endif
static void
memheap_init()
{
    // Check if we should crash the process on mutex pass
    bool crash_on_mutex_pass = memalloc_get_bool_env(g_crash_on_mutex_pass_str);
    memlock_init(&g_memheap_lock, crash_on_mutex_pass);
#ifndef _WIN32
    pthread_atfork(memheap_prefork, memheap_postfork_parent, memheap_postfork_child);
#endif
}

static uint32_t
heap_tracker_next_sample_size(uint32_t sample_size)
{
    /* Get a value between [0, 1[ */
    double q = (double)rand() / ((double)RAND_MAX + 1);
    /* Get a value between ]-inf, 0[, more likely close to 0 */
    double log_val = log2(q);
    return (uint32_t)(log_val * (-log(2) * (sample_size + 1)));
}

static void
heap_tracker_init(heap_tracker_t* heap_tracker)
{
    heap_tracker->allocs_m = memalloc_heap_map_new();
    heap_tracker->freezer.allocs_m = memalloc_heap_map_new();
    ptr_array_init(&heap_tracker->freezer.frees);
    heap_tracker->allocated_memory = 0;
    heap_tracker->frozen = false;
    heap_tracker->sample_size = 0;
    heap_tracker->current_sample_size = 0;
}

static void
heap_tracker_wipe(heap_tracker_t* heap_tracker)
{
    memalloc_heap_map_delete(heap_tracker->allocs_m);
    memalloc_heap_map_delete(heap_tracker->freezer.allocs_m);
    ptr_array_wipe(&heap_tracker->freezer.frees);
}

static void
heap_tracker_freeze(heap_tracker_t* heap_tracker)
{
    heap_tracker->frozen = true;
}

static void
heap_tracker_untrack_thawed(heap_tracker_t* heap_tracker, void* ptr)
{
    traceback_t* tb = memalloc_heap_map_remove(heap_tracker->allocs_m, ptr);
    if (tb) {
        traceback_free(tb);
    }
}

static void
heap_tracker_thaw(heap_tracker_t* heap_tracker)
{
    memalloc_heap_map_destructive_copy(heap_tracker->allocs_m, heap_tracker->freezer.allocs_m);

    /* Handle the frees: we need to handle the frees after we merge the allocs
       array together to be sure that there's no free in the freezer matching
       an alloc that is also in the freezer; heap_tracker_untrack_thawed does
       not care about the freezer, by definition. */
    for (MEMALLOC_HEAP_PTR_ARRAY_COUNT_TYPE i = 0; i < heap_tracker->freezer.frees.count; i++) {
        heap_tracker_untrack_thawed(heap_tracker, heap_tracker->freezer.frees.tab[i]);
    }

    /* Reset the count to zero so we can reused the array and overwrite previous values */
    heap_tracker->freezer.frees.count = 0;

    heap_tracker->frozen = false;
}

/* Public API */

void
memalloc_heap_tracker_init(uint32_t sample_size)
{

    if (memlock_trylock(&g_memheap_lock)) {
        heap_tracker_init(&global_heap_tracker);
        global_heap_tracker.sample_size = sample_size;
        global_heap_tracker.current_sample_size = heap_tracker_next_sample_size(sample_size);
        memlock_unlock(&g_memheap_lock);
    }
}

void
memalloc_heap_tracker_deinit(void)
{
    if (memlock_trylock(&g_memheap_lock)) {
        heap_tracker_wipe(&global_heap_tracker);
        memlock_unlock(&g_memheap_lock);
    }
}

void
memalloc_heap_untrack(void* ptr)
{
    if (!memlock_trylock(&g_memheap_lock)) {
        return;
    }
    if (global_heap_tracker.frozen) {
        /* NB: because we also take the lock in memalloc_heap, we're not going to
         * take this branch. We will, however, if we start using the GIL as our
         * lock instead.
         */
        traceback_t* tb = memalloc_heap_map_remove(global_heap_tracker.freezer.allocs_m, ptr);
        if (tb) {
            traceback_free(tb);
        } else if (memalloc_heap_map_contains(global_heap_tracker.allocs_m, ptr)) {
            /* We're tracking this pointer but can't remove it right now because
             * we're iterating over the map. Save the pointer to remove later */
            ptr_array_append(&global_heap_tracker.freezer.frees, ptr);
        }
    } else {
        heap_tracker_untrack_thawed(&global_heap_tracker, ptr);
    }

    memlock_unlock(&g_memheap_lock);
}

/* Track a memory allocation in the heap profiler.

   Returns true if the allocation was tracked, false otherwise. */
bool
memalloc_heap_track(uint16_t max_nframe, void* ptr, size_t size, PyMemAllocatorDomain domain)
{
    /* Heap tracking is disabled */
    if (global_heap_tracker.sample_size == 0)
        return false;

    /* Check for overflow */
    uint64_t res = atomic_add_clamped(&global_heap_tracker.allocated_memory, size, MAX_HEAP_SAMPLE_SIZE);
    if (0 == res)
        return false;

    // Take the lock
    if (!memlock_trylock(&g_memheap_lock)) {
        return false;
    }

    /* Check if we have enough sample or not */
    if (global_heap_tracker.allocated_memory < global_heap_tracker.current_sample_size) {
        memlock_unlock(&g_memheap_lock);
        return false;
    }

    if (memalloc_heap_map_size(global_heap_tracker.allocs_m) +
          memalloc_heap_map_size(global_heap_tracker.freezer.allocs_m) >
        TRACEBACK_ARRAY_MAX_COUNT) {
        /* TODO(nick) this is vestigial from the original array-based
         * implementation. Do we actually want this? It gives us bounded memory
         * use, but the size limit is arbitrary and once we hit the arbitrary
         * limit our reported numbers will be inaccurate.
         */
        memlock_unlock(&g_memheap_lock);
        return false;
    }

    /* Avoid loops */
    if (!memalloc_take_guard()) {
        memlock_unlock(&g_memheap_lock);
        return false;
    }

    traceback_t* tb = memalloc_get_traceback(max_nframe, ptr, global_heap_tracker.allocated_memory, domain);
    if (tb) {
        if (global_heap_tracker.frozen) {
            memalloc_heap_map_insert(global_heap_tracker.freezer.allocs_m, tb->ptr, tb);
        } else {
            memalloc_heap_map_insert(global_heap_tracker.allocs_m, tb->ptr, tb);
        }

        /* Reset the counter to 0 */
        global_heap_tracker.allocated_memory = 0;

        /* Compute the new target sample size */
        global_heap_tracker.current_sample_size = heap_tracker_next_sample_size(global_heap_tracker.sample_size);

        memalloc_yield_guard();
        memlock_unlock(&g_memheap_lock);
        return true;
    }

    memalloc_yield_guard();
    memlock_unlock(&g_memheap_lock);
    return false;
}

PyObject*
memalloc_heap()
{
    if (!memlock_trylock(&g_memheap_lock)) {
        return NULL;
    }

    heap_tracker_freeze(&global_heap_tracker);

    PyObject* heap_list = memalloc_heap_map_export(global_heap_tracker.allocs_m);

    heap_tracker_thaw(&global_heap_tracker);

    memlock_unlock(&g_memheap_lock);
    return heap_list;
}
