#include <math.h>
#include <stdlib.h>

#define PY_SSIZE_T_CLEAN
#include "_memalloc.h"
#include "_memalloc_heap.h"
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
    traceback_array_init(&heap_tracker->allocs);
    traceback_array_init(&heap_tracker->freezer.allocs);
    ptr_array_init(&heap_tracker->freezer.frees);
    heap_tracker->allocated_memory = 0;
    heap_tracker->frozen = false;
    heap_tracker->sample_size = 0;
    heap_tracker->current_sample_size = 0;
}

static void
heap_tracker_wipe(heap_tracker_t* heap_tracker)
{
    traceback_array_wipe(&heap_tracker->allocs);
    traceback_array_wipe(&heap_tracker->freezer.allocs);
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
    /* This search is O(n) where `n` is the number of tracked traceback,
       which is linearly linked to the heap size. This search could probably be
       optimized in a couple of ways:

       - sort the traceback in allocs by ptr so we can find the ptr in O(log2 n)
       - use a Bloom filter?

       That being said, we start iterating at the end of the array because most
       of the time this is where the untracked ptr is (the most recent object
       get de-allocated first usually). This might be a good enough
       trade-off. */
    for (TRACEBACK_ARRAY_COUNT_TYPE i = heap_tracker->allocs.count; i > 0; i--) {
        traceback_t** tb = &heap_tracker->allocs.tab[i - 1];

        if (ptr == (*tb)->ptr) {
            /* Free the traceback */
            traceback_free(*tb);
            traceback_array_remove(&heap_tracker->allocs, tb);
            break;
        }
    }
}

static void
heap_tracker_thaw(heap_tracker_t* heap_tracker)
{
    /* Add the frozen allocs at the end */
    traceback_array_splice(&heap_tracker->allocs,
                           heap_tracker->allocs.count,
                           0,
                           heap_tracker->freezer.allocs.tab,
                           heap_tracker->freezer.allocs.count);

    /* Handle the frees: we need to handle the frees after we merge the allocs
       array together to be sure that there's no free in the freezer matching
       an alloc that is also in the freezer; heap_tracker_untrack_thawed does
       not care about the freezer, by definition. */
    for (MEMALLOC_HEAP_PTR_ARRAY_COUNT_TYPE i = 0; i < heap_tracker->freezer.frees.count; i++)
        heap_tracker_untrack_thawed(heap_tracker, heap_tracker->freezer.frees.tab[i]);

    /* Reset the count to zero so we can reused the array and overwrite previous values */
    heap_tracker->freezer.allocs.count = 0;
    heap_tracker->freezer.frees.count = 0;

    heap_tracker->frozen = false;
}

/* Public API */

void
memalloc_heap_tracker_init(memalloc_context_t* ctx, uint32_t sample_size)
{

    if (memlock_trylock(&ctx->heap_lock)) {
        heap_tracker_init(&ctx->heap_profile);
        ctx->heap_profile.sample_size = sample_size;
        ctx->heap_profile.current_sample_size = heap_tracker_next_sample_size(sample_size);
        memlock_unlock(&ctx->heap_lock);
    }
}

void
memalloc_heap_tracker_deinit(memalloc_context_t* ctx)
{
    if (memlock_trylock(&ctx->heap_lock)) {
        heap_tracker_wipe(&ctx->heap_profile);
        memlock_unlock(&ctx->heap_lock);
    }
}

void
memalloc_heap_untrack(memalloc_context_t* ctx, void* ptr)
{
    if (!memlock_trylock(&ctx->heap_lock)) {
        return;
    }
    if (ctx->heap_profile.frozen) {
        /* Check that we still have space to store the free. If we don't have
           enough space, we ignore the untrack. That's sad as there is a change
           the heap profile won't be valid anymore. However, that's the best we
           can do since reporting an error is not an option here. What's gonna
           free more than 2^64 pointers anyway?!
        */
        if (ctx->heap_profile.freezer.frees.count < MEMALLOC_HEAP_PTR_ARRAY_MAX_COUNT)
            ptr_array_append(&ctx->heap_profile.freezer.frees, ptr);
    } else
        heap_tracker_untrack_thawed(&ctx->heap_profile, ptr);

    memlock_unlock(&ctx->heap_lock);
}

/* Track a memory allocation in the heap profiler.

   Returns true if the allocation was tracked, false otherwise. */
bool
memalloc_heap_track(memalloc_context_t* ctx, uint16_t max_nframe, void* ptr, size_t size, PyMemAllocatorDomain domain)
{
    /* Heap tracking is disabled */
    if (ctx->heap_profile.sample_size == 0)
        return false;

    /* Check for overflow */
    uint64_t res = atomic_add_clamped(&ctx->heap_profile.allocated_memory, size, MAX_HEAP_SAMPLE_SIZE);
    if (0 == res)
        return false;

    // Take the lock
    if (!memlock_trylock(&ctx->heap_lock)) {
        return false;
    }

    /* Check if we have enough sample or not */
    if (ctx->heap_profile.allocated_memory < ctx->heap_profile.current_sample_size) {
        memlock_unlock(&ctx->heap_lock);
        return false;
    }

    /* Check if we can add more samples: the sum of the freezer + alloc tracker
     cannot be greater than what the alloc tracker can handle: when the alloc
     tracker is thawed, all the allocs in the freezer will be moved there!*/
    if (ctx->heap_profile.freezer.allocs.count + ctx->heap_profile.allocs.count >= TRACEBACK_ARRAY_MAX_COUNT) {
        memlock_unlock(&ctx->heap_lock);
        return false;
    }

    /* Avoid loops */
    if (!memalloc_take_guard()) {
        memlock_unlock(&ctx->heap_lock);
        return false;
    }

    /* The weight of the allocation is described above, but briefly: it's the
       count of bytes allocated since the last sample, including this one, which
       will tend to be larger for large allocations and smaller for small
       allocations, and close to the average sampling interval so that the sum
       of sample live allocations stays close to the actual heap size */
    traceback_t* tb = memalloc_get_traceback(max_nframe, ptr, ctx->heap_profile.allocated_memory, domain);
    if (tb) {
        if (ctx->heap_profile.frozen)
            traceback_array_append(&ctx->heap_profile.freezer.allocs, tb);
        else
            traceback_array_append(&ctx->heap_profile.allocs, tb);

        /* Reset the counter to 0 */
        ctx->heap_profile.allocated_memory = 0;

        /* Compute the new target sample size */
        ctx->heap_profile.current_sample_size = heap_tracker_next_sample_size(ctx->heap_profile.sample_size);

        memalloc_yield_guard();
        memlock_unlock(&ctx->heap_lock);
        return true;
    }

    memalloc_yield_guard();
    memlock_unlock(&ctx->heap_lock);
    return false;
}

PyObject*
memalloc_heap(memalloc_context_t* ctx)
{
    if (!memlock_trylock(&ctx->heap_lock)) {
        return NULL;
    }

    heap_tracker_freeze(&ctx->heap_profile);

    PyObject* heap_list = PyList_New(ctx->heap_profile.allocs.count);

    for (TRACEBACK_ARRAY_COUNT_TYPE i = 0; i < ctx->heap_profile.allocs.count; i++) {
        traceback_t* tb = ctx->heap_profile.allocs.tab[i];

        PyObject* tb_and_size = PyTuple_New(2);
        PyTuple_SET_ITEM(tb_and_size, 0, traceback_to_tuple(tb));
        PyTuple_SET_ITEM(tb_and_size, 1, PyLong_FromSize_t(tb->size));
        PyList_SET_ITEM(heap_list, i, tb_and_size);
    }

    heap_tracker_thaw(&ctx->heap_profile);

    memlock_unlock(&ctx->heap_lock);
    return heap_list;
}
