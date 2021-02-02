#define PY_SSIZE_T_CLEAN
#include "_memalloc_heap.h"
#include "_memalloc_tb.h"

typedef struct
{
    traceback_array_t allocs;
    /* Allocated memory counter in bytes */
    uint32_t allocated_memory;
} heap_tracker_t;

static heap_tracker_t global_heap_tracker;

static void
heap_tracker_init(heap_tracker_t* heap_tracker)
{
    traceback_array_init(&heap_tracker->allocs);
    heap_tracker->allocated_memory = 0;
}

static void
heap_tracker_wipe(heap_tracker_t* heap_tracker)
{
    traceback_array_wipe(&heap_tracker->allocs);
}

void
memalloc_heap_tracker_init(void)
{
    heap_tracker_init(&global_heap_tracker);
}

void
memalloc_heap_tracker_deinit(void)
{
    heap_tracker_wipe(&global_heap_tracker);
}

void
memalloc_heap_untrack(void* ptr)
{
    /* This search is O(n) where `n` is the number of tracked traceback,
       which is linearly linked to the heap size. This search could probably be
       optimized in a couple of ways:

       - sort the traceback in allocs by ptr so we can find the ptr in O(log2 n)
       - use a Bloom filter?

       That being said, we start iterating at the end of the array because most
       of the time this is where the untracked ptr is (the most recent object
       get de-allocated first usually). This might be a good enough
       trade-off. */
    for (TRACEBACK_ARRAY_COUNT_TYPE i = global_heap_tracker.allocs.count; i > 0; i--) {
        traceback_t** tb = &global_heap_tracker.allocs.tab[i - 1];

        if (ptr == (*tb)->ptr) {
            /* Free the traceback */
            traceback_free(*tb);
            traceback_array_remove(&global_heap_tracker.allocs, tb);
            break;
        }
    }
}

void
memalloc_heap_track(uint32_t heap_sample_size, uint16_t max_nframe, void* ptr, size_t size)
{
    /* Heap tracking is disabled */
    if (heap_sample_size == 0)
        return;

    /* Check for overflow */
    global_heap_tracker.allocated_memory = Py_MIN(global_heap_tracker.allocated_memory + size, MAX_HEAP_SAMPLE_SIZE);

    /* Check if we have enough sample or not */
    if (global_heap_tracker.allocated_memory < heap_sample_size)
        return;

    /* Cannot add more sample */
    if (global_heap_tracker.allocs.count >= TRACEBACK_ARRAY_MAX_COUNT)
        return;

    traceback_t* tb = memalloc_get_traceback(max_nframe, ptr, global_heap_tracker.allocated_memory);
    if (tb) {
        traceback_array_append(&global_heap_tracker.allocs, tb);
        /* Reset the counter to 0 */
        global_heap_tracker.allocated_memory = 0;
    }
}
