#include <math.h>
#include <stdlib.h>

#define PY_SSIZE_T_CLEAN
#include "_memalloc_heap.h"
#include "_memalloc_heap_map.h"
#include "_memalloc_reentrant.h"
#include "_memalloc_tb.h"

/*
   How heap profiler sampling works:

   This is mostly derived from
 https://github.com/google/tcmalloc/blob/master/docs/sampling.md#detailed-treatment-of-weighting-weighting

   We want to explain memory used by the program. We can't track every
   allocation with reasonable overhead, so we sample. We'd like the heap to
   represent what's taking up the most memory. We'd like to see large live
   allocations, or when many small allocations in some part of the code add up
   to a lot of memory usage. So, we choose to sample based on bytes allocated.
   We basically want every byte allocated to have the same probability of being
   represented in the profile. Assume we want an average of one byte out of
   every R allocated sampled. Call R the "sampling interval". In a simplified
   world where every allocation is 1 byte, we can just do a 1/R coin toss for
   every allocation.  This can be simplified by observing that the interval
   between samples done this way follows a geometric distribution with average
   R. We can draw from a geometric distribution to pick the next sample point.
   For computational simplicity, we use an exponential distribution, which is
   essentially the limit of the geometric distribution if we were to divide each
   byte into smaller and smaller sub-bytes. We set a target for sampling, T,
   drawn from the exponential distribution with average R. We count the number
   of bytes allocated, C. For each allocation, we increment C by the size of the
   allocation, and when C >= T, we take a sample, reset C to 0, and re-draw T.

   If we reported just the sampled allocation's sizes, we would significantly
   misrepresent the actual heap size. We're probably going to hit some small
   allocations with our sampling, and reporting their actual size would
   under-represent the size of the heap. Each sampled allocation represents
   roughly R bytes of actual allocated memory. We want to weight our samples
   accordingly, and account for the fact that large allocations are more likely
   to be sampled than small allocations.

   The math for weighting is described in more detail in the tcmalloc docs.
   Basically, any sampled allocation should get an average weight of R, our
   sampling interval. However, this would under-weight allocations larger than R
   bytes, our sampling interval. When we pick the next sampling point, it's
   probably going to be in the middle of an allocation. Bytes of the sampled
   allocation past that point are going to be skipped by our sampling method,
   since we re-draw the target _after_ the allocation. We can correct for this
   by looking at how big the allocation was, and how much it would drive the
   counter C past the target T. The formula W = R + (C - T) expresses this,
   where C is the counter including the sampled allocation. If the allocation
   was large, we are likely to have significantly exceeded T, so the weight will
   be larger. Conversely, if the allocation was small, C - T will likely be
   small, so the allocation gets less weight, and as we get closer to our
   hypothetical 1-byte allocations we'll get closer to a weight of R for each
   allocation. The current code simplifies this a bit. We can also express the
   weight as C + (R - T), and note that on average T should equal R, and just
   drop the (R - T) term and use C as the weight. We might want to use the full
   formula if more testing shows us to be too inaccurate.
 */

typedef struct
{
    /* Heap profiler sampling interval */
    uint64_t sample_size;
    /* Next heap sample target, in bytes allocated */
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

static heap_tracker_t global_heap_tracker;

static uint32_t
heap_tracker_next_sample_size(uint32_t sample_size)
{
    /* We want to draw a sampling target from an exponential distribution with
       average sample_size. We use the standard technique of inverse transform
       sampling, where we take uniform randomness, which is easy to get, and
       transform it by the inverse of the cumulative distribution function for
       the distribution we want to sample.
       See https://en.wikipedia.org/wiki/Inverse_transform_sampling. */
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
    heap_tracker_init(&global_heap_tracker);
    global_heap_tracker.sample_size = sample_size;
    global_heap_tracker.current_sample_size = heap_tracker_next_sample_size(sample_size);
}

void
memalloc_heap_tracker_deinit(void)
{
    /* Setting the sample size back to zero acts as a flag that the profiler is
     * deactivated. Checked during sampling, in case sampling and
     * deinitialization interleave due to GIL release.
     * NB: do this before wiping, in case deallocating tracebacks leads to GIL
     * release
     */
    global_heap_tracker.sample_size = 0;
    heap_tracker_wipe(&global_heap_tracker);
}

void
memalloc_heap_untrack(void* ptr)
{
    if (global_heap_tracker.sample_size == 0) {
        /* The profiler was stopped. See memalloc_realloc, for example,
         * where we might have a GIL release in when sampling for the allocation
         * profiler prior to untracking the previous allocation. */
        return;
    }
    if (global_heap_tracker.frozen) {
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
}

/* Track a memory allocation in the heap profiler. */
void
memalloc_heap_track(uint16_t max_nframe, void* ptr, size_t size, PyMemAllocatorDomain domain)
{
    /* Heap tracking is disabled */
    if (global_heap_tracker.sample_size == 0)
        return;

    global_heap_tracker.allocated_memory += size;

    /* Check if we have enough sample or not */
    if (global_heap_tracker.allocated_memory < global_heap_tracker.current_sample_size) {
        return;
    }

    if (memalloc_heap_map_size(global_heap_tracker.allocs_m) +
          memalloc_heap_map_size(global_heap_tracker.freezer.allocs_m) >
        TRACEBACK_ARRAY_MAX_COUNT) {
        /* TODO(nick) this is vestigial from the original array-based
         * implementation. Do we actually want this? It gives us bounded memory
         * use, but the size limit is arbitrary and once we hit the arbitrary
         * limit our reported numbers will be inaccurate.
         */
        return;
    }

    /* Avoid loops */
    if (!memalloc_take_guard()) {
        return;
    }

    /* Taking a traceback, GIL may be released, end of "atomic" state manipulation */

    /* The weight of the allocation is described above, but briefly: it's the
       count of bytes allocated since the last sample, including this one, which
       will tend to be larger for large allocations and smaller for small
       allocations, and close to the average sampling interval so that the sum
       of sample live allocations stays close to the actual heap size */
    traceback_t* tb = memalloc_get_traceback(max_nframe, ptr, global_heap_tracker.allocated_memory, domain);
    if (!tb) {
        goto done;
    }

    /* Done taking a traceback, assume we have the GIL again until the next
     * C Python API call */

    if (global_heap_tracker.sample_size == 0) {
        /* If getting a traceback lead to a GIL release, it is possible
         * that the profiler was stopped and the heap map was freed.
         * Don't add the sample in that case. */
        traceback_free(tb);
        goto done;
    }

    if (global_heap_tracker.frozen) {
        memalloc_heap_map_insert(global_heap_tracker.freezer.allocs_m, tb->ptr, tb);
    } else {
        memalloc_heap_map_insert(global_heap_tracker.allocs_m, tb->ptr, tb);
    }

    /* Reset the counter to 0 */
    global_heap_tracker.allocated_memory = 0;

    /* Compute the new target sample size */
    global_heap_tracker.current_sample_size = heap_tracker_next_sample_size(global_heap_tracker.sample_size);

done:
    memalloc_yield_guard();
}

PyObject*
memalloc_heap(void)
{
    heap_tracker_freeze(&global_heap_tracker);

    /* After this point, we _may_ have a GIL release during any of the C Python
     * API calls while building the list for export. This could allow other
     * threads to run, mutating the tracked allocations. This is why we "froze"
     * the tracker above.  Allocations between now and when we "thaw" will go in
     * a secondary tracker, so the primary tracker won't be mutated.
     */
    PyObject* heap_list = memalloc_heap_map_export(global_heap_tracker.allocs_m);

    heap_tracker_thaw(&global_heap_tracker);

    return heap_list;
}
