#include <cassert>
#include <cmath>
#include <cstdlib>
#include <cstring>

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include "_memalloc_gc_guard.hpp"
#include "_memalloc_heap.h"
#include "_memalloc_reentrant.h"

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

// Pool implementation
// _invokes_cpython suffix: calls traceback_t::reset() and constructor which invoke CPython APIs
std::unique_ptr<traceback_t>
heap_tracker_t::pool_get_with_alloc_data_invokes_cpython(size_t size, size_t weighted_size, uint16_t max_nframe)
{
    /* Try to get a traceback from the pool */
    if (MEMALLOC_LIKELY(!pool.empty())) {
        auto tb = std::move(pool.back());
        pool.pop_back();
        /* Initialize it with the new allocation data */
        tb->init_sample(size, weighted_size, max_nframe);
        return tb;
    }

    /* Pool is empty, create a new traceback */
    return std::make_unique<traceback_t>(size, weighted_size, max_nframe);
}

void
heap_tracker_t::pool_put_no_cpython(std::unique_ptr<traceback_t> tb)
{
    if (MEMALLOC_UNLIKELY(!tb)) {
        return;
    }

    /* Clear buffers before returning to pool to prevent memory leaks */
    tb->sample.clear();

    /* Reset list pointers and metadata */
    tb->list_next = nullptr;
    tb->list_prev = nullptr;
    tb->user_ptr = nullptr;
    tb->alloc_size = 0;

    /* Try to return the traceback to the pool */
    if (MEMALLOC_LIKELY(pool.size() < POOL_CAPACITY)) {
        pool.push_back(std::move(tb));
    }
    /* If pool is full, tb automatically deletes the traceback when it goes out of scope */
}

uint32_t
heap_tracker_t::next_sample_size_no_cpython(uint32_t sample_size)
{
    /* Draw a sampling target from an exponential distribution with mean
       sample_size. std::exponential_distribution handles the inverse-transform
       sampling internally.

       NOTE: std::exponential_distribution calls log internally. log is not
       listed as async-signal-safe by POSIX, but does not use locks in practice.
       We assume it is safe to call from heap_tracker_t::postfork_child. */
    std::exponential_distribution<double> dist(1.0 / (sample_size + 1));
    return static_cast<uint32_t>(dist(rng));
}

// Intrusive list operations
void
heap_tracker_t::list_link_no_cpython(traceback_t* tb)
{
    tb->list_prev = nullptr;
    tb->list_next = allocs_head;
    if (allocs_head) {
        allocs_head->list_prev = tb;
    }
    allocs_head = tb;
    allocs_count++;
}

void
heap_tracker_t::list_unlink_no_cpython(traceback_t* tb)
{
    if (tb->list_prev) {
        tb->list_prev->list_next = tb->list_next;
    } else {
        allocs_head = tb->list_next;
    }
    if (tb->list_next) {
        tb->list_next->list_prev = tb->list_prev;
    }
    tb->list_next = nullptr;
    tb->list_prev = nullptr;
    allocs_count--;
}

// Method implementations
heap_tracker_t::heap_tracker_t(uint32_t sample_size_val)
  : sample_size(sample_size_val)
  , rng(sample_size_val != 0U ? sample_size_val : 0x9e3779b9U) // 2^32 / phi (golden ratio)
  , current_sample_size(next_sample_size_no_cpython(sample_size_val))
  , allocated_memory(0)
{
    pool.reserve(POOL_CAPACITY); // Pre-allocate pool capacity to avoid reallocations
}

void
heap_tracker_t::add_sample_no_cpython(void* user_ptr, size_t alloc_size, std::unique_ptr<traceback_t> tb)
{
    memalloc_gil_debug_guard_t guard(gil_guard);

    /* Write the header at user_ptr - MEMALLOC_HEADER_SIZE */
    memalloc_header_t* header =
      reinterpret_cast<memalloc_header_t*>(static_cast<char*>(user_ptr) - MEMALLOC_HEADER_SIZE);
    header->signature = MEMALLOC_SIGNATURE;
    header->metadata_ptr = tb.get();

    /* Store user_ptr and alloc_size in the traceback for realloc and postfork */
    tb->user_ptr = user_ptr;
    tb->alloc_size = alloc_size;

    /* Link into intrusive list */
    traceback_t* raw_tb = tb.release(); // Transfer ownership to the list
    list_link_no_cpython(raw_tb);

    // Get ready for the next sample
    reset_sampling_state_no_cpython();
}

void
heap_tracker_t::untrack_from_header_no_cpython(traceback_t* tb)
{
    memalloc_gil_debug_guard_t guard(gil_guard);

    /* Unlink from intrusive list */
    list_unlink_no_cpython(tb);

    /* Return to pool (wrapping in unique_ptr for pool_put) */
    pool_put_no_cpython(std::unique_ptr<traceback_t>(tb));
}

void
heap_tracker_t::export_heap_no_cpython()
{
    memalloc_gil_debug_guard_t guard(gil_guard);

    /* Iterate over live samples via the intrusive list and export them */
    for (traceback_t* tb = allocs_head; tb != nullptr; tb = tb->list_next) {
        tb->sample.export_sample();
    }

    Datadog::Sample::profile_borrow().stats().set_heap_tracker_size(allocs_count);
}

void
heap_tracker_t::reset_sampling_state_no_cpython()
{
    allocated_memory = 0;
    current_sample_size = next_sample_size_no_cpython(sample_size);
}

void
heap_tracker_t::postfork_child()
{
    // As we're in the child process after fork, we want to make sure that the
    // heap tracker state is consistent before running any Python code. If not,
    // we may end up triggering memory profiler code with an inconsistent state,
    // leading to undefined behaviors and/or crashes, ref: incident-48649.
    // To avoid this, we clear the heap tracker state here.

    // Sample pool contains traceback_t objects, which reference the global
    // Profile state. Global Profile state is reset after fork in
    // Profile::postfork_child()
    pool.clear();

    // Walk the intrusive list: null out metadata pointers and delete traceback_t objects.
    // IMPORTANT: Keep signatures intact so the child's free() still knows these
    // allocations have prepended headers and will free real_ptr (not user_ptr).
    traceback_t* tb = allocs_head;
    while (tb) {
        traceback_t* next = tb->list_next;

        if (tb->user_ptr) {
            memalloc_header_t* header =
              reinterpret_cast<memalloc_header_t*>(static_cast<char*>(tb->user_ptr) - MEMALLOC_HEADER_SIZE);
            header->metadata_ptr = nullptr;
        }

        delete tb;
        tb = next;
    }
    allocs_head = nullptr;
    allocs_count = 0;

    // Reset the sampling state to start fresh after fork.
    reset_sampling_state_no_cpython();
}

void
heap_tracker_t::clear_all_no_cpython()
{
    traceback_t* tb = allocs_head;
    while (tb) {
        traceback_t* next = tb->list_next;

        if (tb->user_ptr) {
            /* IMPORTANT: Keep the signature intact so that free() still knows
             * this allocation has a prepended header and will free real_ptr
             * (user_ptr - 16) instead of user_ptr. Only null the metadata_ptr
             * to prevent use-after-free on the traceback_t. */
            memalloc_header_t* header =
              reinterpret_cast<memalloc_header_t*>(static_cast<char*>(tb->user_ptr) - MEMALLOC_HEADER_SIZE);
            header->metadata_ptr = nullptr;
        }

        delete tb;
        tb = next;
    }
    allocs_head = nullptr;
    allocs_count = 0;
}

// Static member definition
heap_tracker_t* heap_tracker_t::instance = nullptr;

/* Public API */

bool
memalloc_heap_tracker_init_no_cpython(uint32_t sample_size)
{
    // TODO(dsn): what should we do it this was already initialized?
    if (!heap_tracker_t::instance) {
        heap_tracker_t::instance = new heap_tracker_t(sample_size);
        return true;
    }
    return false;
}

void
memalloc_heap_tracker_deinit_no_cpython(void)
{
    if (!heap_tracker_t::instance) {
        return;
    }

    /* Walk the list and clear all signatures before destroying the tracker.
     * This is needed because sampled allocations may still be live after stop(),
     * and their headers must not contain dangling metadata pointers. */
    heap_tracker_t::instance->clear_all_no_cpython();

    // Delete the instance and set to nullptr. We set to nullptr first so that
    // if the destructor releases the GIL, we can use nullptr as a sentinel.
    heap_tracker_t* old_instance = heap_tracker_t::instance;
    heap_tracker_t::instance = nullptr;
    delete old_instance;
}

void
memalloc_heap_untrack_from_header_no_cpython(void* metadata_ptr)
{
    if (MEMALLOC_UNLIKELY(!heap_tracker_t::instance || !metadata_ptr)) {
        return;
    }
    heap_tracker_t::instance->untrack_from_header_no_cpython(static_cast<traceback_t*>(metadata_ptr));
}

/* Track a memory allocation in the heap profiler.
 * Called AFTER should_sample returned true and the allocation (with header space)
 * has been performed. user_ptr points to the user-visible region (real_ptr + 16).
 * allocated_memory_val is the weight from should_sample. */
void
memalloc_heap_track_invokes_cpython(uint16_t max_nframe,
                                    void* user_ptr,
                                    size_t size,
                                    uint64_t allocated_memory_val,
                                    PyMemAllocatorDomain domain)
{
    (void)domain; // Parameter kept for API consistency but not currently used
    if (MEMALLOC_UNLIKELY(!heap_tracker_t::instance)) {
        return;
    }

    /* Skip tracking if we're already inside the malloc hook on this thread.
     * Reentrant tracking would corrupt the heap tracker's data structures. */
    memalloc_reentrant_guard_t guard;
    if (MEMALLOC_UNLIKELY(!guard)) {
        return;
    }

    /* Prior to Python 3.12, and particularly in Python 3.11, collecting
       tracebacks while intercepting allocations is prone to crashes. We
       currently use the C Python API to collect tracebacks, which can
       do allocations. These allocations can in turn trigger garbage collection,
       allowing other code to run. In the past we've seen this lead to
       GIL release and cause corruption in the memory profiler.

       This can also lead to use-after-free crashes. For example, calling
       realloc to grow a data structure, we can trigger garbage collection which
       visits the data structure. The underlying reallocaiton will have already
       happened (we want to track the new address) but the data structure will
       still point to old memory since our wrapper hasn't returned.

       Python 3.12 doesn't trigger GC during allocation. Instead, a flag is set
       for GC to run later, at a safe point in the interpreter. But for earlier
       versions, we disable GC temporarily. This will allow a small, temporary
       increase in memory usage during sampling. But it is overall cheap (mostly
       just toggling a boolean) and the alternative is hard-to-diagnose crashes.

       RAII guard automatically re-enables GC when it goes out of scope. */
#if defined(_PY310_AND_LATER) && !defined(_PY312_AND_LATER)
    pygc_temp_disable_guard_t gc_guard;
#endif // defined(_PY310_AND_LATER) && !defined(_PY312_AND_LATER)

    /* The weight of the allocation is described above, but briefly: it's the
       count of bytes allocated since the last sample, including this one, which
       will tend to be larger for large allocations and smaller for small
       allocations, and close to the average sampling interval so that the sum
       of sample live allocations stays close to the actual heap size */

    // Check that instance is valid before creating traceback
    if (MEMALLOC_UNLIKELY(!heap_tracker_t::instance)) {
        return;
    }

    auto tb =
      heap_tracker_t::instance->pool_get_with_alloc_data_invokes_cpython(size, allocated_memory_val, max_nframe);

    // Export allocation sample right away to avoid holding it
    tb->sample.export_sample();
    // Reset the allocation data, keep heap data for tracking
    tb->sample.reset_alloc();
    // pool_get_with_alloc_data_invokes_cpython() creates sample with allocation data only (no heap data)
    // to avoid double-pushing allocation data, we manually push heap data here
    // TODO(dsn): figure out if this actually makes sense, or if we should use the weighted size
    tb->sample.push_heap(size);

    // Check that instance is still valid after GIL release in constructor
    if (MEMALLOC_LIKELY(heap_tracker_t::instance != nullptr)) {
        heap_tracker_t::instance->add_sample_no_cpython(user_ptr, size, std::move(tb));
    }
    // If instance is gone, tb's unique_ptr automatically deletes the traceback
}

void
memalloc_heap_no_cpython(void)
{
    if (MEMALLOC_LIKELY(heap_tracker_t::instance != nullptr)) {
        heap_tracker_t::instance->export_heap_no_cpython();
    }
}

void
memalloc_heap_postfork_child(void)
{
    if (MEMALLOC_LIKELY(heap_tracker_t::instance != nullptr)) {
        heap_tracker_t::instance->postfork_child();
    }
}
