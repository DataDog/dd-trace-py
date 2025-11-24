#include <cassert>
#include <cmath>
#include <cstdlib>
#include <vector>

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include "_memalloc_debug.h"
#include "_memalloc_heap.h"
#include "_memalloc_heap_map.hpp"
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

class heap_tracker_t
{
  public:
    /* Constructor - does not make any C Python API calls */
    heap_tracker_t(uint32_t sample_size_val);
    ~heap_tracker_t();

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
    bool should_sample_no_cpython(size_t size, uint64_t* allocated_memory_val);

    /* Track an allocation that we decided to sample. This updates shared state and
     * must be called with the GIL held and without making any C Python API calls.
     * If an allocation at the same address is already tracked, the old traceback
     * is deleted internally. */
    void add_sample_no_cpython(traceback_t* tb);

    void export_heap_no_cpython();

    /* Global instance of the heap tracker */
    static heap_tracker_t* instance;

  private:
    static uint32_t next_sample_size(uint32_t sample_size);

    /* Heap profiler sampling interval */
    uint64_t sample_size;
    /* Next heap sample target, in bytes allocated */
    uint64_t current_sample_size;
    /* Tracked allocations */
    memalloc_heap_map allocs_m;
    /* Bytes allocated since the last sample was collected */
    uint64_t allocated_memory;

    /* Debug guard to assert that GIL-protected critical sections are maintained
     * while accessing the profiler's state */
    memalloc_gil_debug_check_t gil_guard;
};

// Static helper function
uint32_t
heap_tracker_t::next_sample_size(uint32_t sample_size)
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

// Method implementations
heap_tracker_t::heap_tracker_t(uint32_t sample_size_val)
  : sample_size(sample_size_val)
  , current_sample_size(next_sample_size(sample_size_val))
  , allocated_memory(0)
{
    // gil_guard and allocs_m are initialized by their constructors
}

heap_tracker_t::~heap_tracker_t() = default;

void
heap_tracker_t::untrack_no_cpython(void* ptr)
{
    memalloc_gil_debug_guard_t guard(gil_guard);
    traceback_t* tb = allocs_m.remove(ptr);
    if (tb && !tb->reported) {
        /* If the sample hasn't been reported yet, set heap size to zero and export it */
        tb->sample.reset_heap();
        tb->sample.export_sample();
        tb->reported = true;
    }
    delete tb; // Safe to delete nullptr
}

bool
heap_tracker_t::should_sample_no_cpython(size_t size, uint64_t* allocated_memory_val)
{
    memalloc_gil_debug_guard_t guard(gil_guard);
    allocated_memory += size;
    *allocated_memory_val = allocated_memory;

    /* Check if we have enough sample or not */
    if (allocated_memory < current_sample_size) {
        return false;
    }

    if (allocs_m.size() > TRACEBACK_ARRAY_MAX_COUNT) {
        /* TODO(nick) this is vestigial from the original array-based
         * implementation. Do we actually want this? It gives us bounded memory
         * use, but the size limit is arbitrary and once we hit the arbitrary
         * limit our reported numbers will be inaccurate.
         */
        return false;
    }

    return true;
}

void
heap_tracker_t::add_sample_no_cpython(traceback_t* tb)
{
    memalloc_gil_debug_guard_t guard(gil_guard);
    allocs_m.insert(tb->ptr, tb);

    /* Reset the counter to 0 */
    allocated_memory = 0;

    /* Compute the new target sample size */
    current_sample_size = next_sample_size(sample_size);
}

void
heap_tracker_t::export_heap_no_cpython()
{
    memalloc_gil_debug_guard_t guard(gil_guard);

    /* Iterate over live samples and mark them as reported */
    for (const auto& pair : allocs_m) {
        traceback_t* tb = pair.second;

        if (tb->reported) {
            tb->sample.reset_alloc();
        }
        tb->sample.export_sample();
        tb->reported = true;
    }
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
    // Delete the instance and set to nullptr. We set to nullptr first so that
    // if the destructor releases the GIL, we can use nullptr as a sentinel.
    heap_tracker_t* old_instance = heap_tracker_t::instance;
    heap_tracker_t::instance = nullptr;
    delete old_instance;
}

void
memalloc_heap_untrack_no_cpython(void* ptr)
{
    if (heap_tracker_t::instance) {
        heap_tracker_t::instance->untrack_no_cpython(ptr);
    }
}

/* Track a memory allocation in the heap profiler. */
void
memalloc_heap_track(uint16_t max_nframe, void* ptr, size_t size, PyMemAllocatorDomain domain)
{
    if (!heap_tracker_t::instance) {
        return;
    }
    uint64_t allocated_memory_val = 0;
    if (!heap_tracker_t::instance->should_sample_no_cpython(size, &allocated_memory_val)) {
        return;
    }

    /* Avoid loops */
    memalloc_reentrant_guard_t guard;
    if (!guard) {
        return;
    }

#if defined(_PY310_AND_LATER) && !defined(_PY312_AND_LATER)
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
     */
    int gc_enabled = PyGC_Disable();
#endif

    /* The weight of the allocation is described above, but briefly: it's the
       count of bytes allocated since the last sample, including this one, which
       will tend to be larger for large allocations and smaller for small
       allocations, and close to the average sampling interval so that the sum
       of sample live allocations stays close to the actual heap size */
    traceback_t* tb = new traceback_t(ptr, size, domain, allocated_memory_val, max_nframe);

#if defined(_PY310_AND_LATER) && !defined(_PY312_AND_LATER)
    if (gc_enabled) {
        PyGC_Enable();
    }
#endif

    // Check that instance is still valid after GIL release in constructor
    if (heap_tracker_t::instance) {
        heap_tracker_t::instance->add_sample_no_cpython(tb);
    } else {
        delete tb;
    }
}

void
memalloc_heap_no_cpython(void)
{
    if (heap_tracker_t::instance) {
        heap_tracker_t::instance->export_heap_no_cpython();
    }
}
