#include <cassert>
#include <cmath>
#include <cstdlib>
#include <memory>
#include <vector>

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include "_memalloc_gc_guard.hpp"
#include "_memalloc_heap_profiler.h"
#include "_memalloc_heap_sample.h"
#include "_memalloc_reentrant.h"
#include "_pymacro.h"

// Static member definition
heap_profiler_t* heap_profiler_t::instance = nullptr;

// Static helper function
uint32_t
heap_profiler_t::next_sample_interval_no_cpython(uint32_t sample_interval)
{
    /* Draw a sampling target from an exponential distribution with average sample_interval */
    double q = (double)rand() / ((double)RAND_MAX + 1);
    double log_val = log2(q);
    return (uint32_t)(log_val * (-log(2) * (sample_interval + 1)));
}

// Constructor
heap_profiler_t::heap_profiler_t(uint32_t sample_interval_val)
  : sample_interval(sample_interval_val)
  , current_sample_interval(next_sample_interval_no_cpython(sample_interval_val))
  , allocated_memory(0)
{
    pool.reserve(POOL_CAPACITY); // Pre-allocate pool capacity
}

// Pool implementation
std::unique_ptr<heap_sample_t>
heap_profiler_t::pool_get_invokes_cpython(size_t size, uint16_t max_nframe)
{
    /* Try to get a sample from the pool */
    if (!pool.empty()) {
        auto sample = std::move(pool.back());
        pool.pop_back();
        /* Reset it with the new heap data - we can't easily "reset" a sample, so just create new */
        return std::make_unique<heap_sample_t>(size, max_nframe);
    }

    /* Pool is empty, create a new sample */
    return std::make_unique<heap_sample_t>(size, max_nframe);
}

void
heap_profiler_t::pool_put_no_cpython(std::unique_ptr<heap_sample_t> sample)
{
    if (!sample) {
        return;
    }

    /* Try to return the sample to the pool */
    if (pool.size() < POOL_CAPACITY) {
        pool.push_back(std::move(sample));
    }
    /* If pool is full, sample automatically deletes when it goes out of scope */
}

void
heap_profiler_t::untrack_no_cpython(void* ptr)
{
    auto node = live_allocs.extract(ptr);
    if (node.empty()) {
        return;
    }

    std::unique_ptr<heap_sample_t> sample = std::move(node.mapped());
    if (sample) {
        /* Export heap=0 event to indicate allocation was freed */
        sample->sample.reset_heap();
        sample->sample.export_sample();
    }
    pool_put_no_cpython(std::move(sample));
}

bool
heap_profiler_t::should_sample_no_cpython(size_t size)
{
    allocated_memory += size;

    /* Check if we have enough bytes allocated to trigger a sample */
    if (allocated_memory < current_sample_interval) {
        return false;
    }

    if (live_allocs.size() > TRACEBACK_ARRAY_MAX_COUNT) {
        /* Bounded memory use - don't track more than max count */
        return false;
    }

    return true;
}

void
heap_profiler_t::track_allocation_invokes_cpython(void* ptr, size_t size, uint16_t max_nframe)
{
    /* Avoid loops */
    memalloc_reentrant_guard_t guard;
    if (!guard) {
        return;
    }

/* Prior to Python 3.12, temporarily disable GC during sample creation
 * to avoid crashes from GC running during allocation interception */
#if defined(_PY310_AND_LATER) && !defined(_PY312_AND_LATER)
    pygc_temp_disable_guard_t gc_guard;
#endif

    /* Create heap sample - this invokes CPython APIs */
    auto sample = pool_get_invokes_cpython(size, max_nframe);

    /* Export initial heap event with size */
    if (sample) {
        sample->sample.export_sample();
    }

    /* Track for later untracking on free */
    if (heap_profiler_t::instance) {
        auto [it, inserted] = live_allocs.insert_or_assign(ptr, std::move(sample));
        (void)it; // Unused

        /* This should always be a new insertion */
        assert(inserted && "track_allocation: found existing entry that should have been removed");
    }

    /* Get ready for the next sample */
    allocated_memory = 0;
    current_sample_interval = next_sample_interval_no_cpython(sample_interval);
}

void
heap_profiler_t::export_heap_no_cpython()
{
    /* Iterate over live allocations and export their current heap state */
    for (const auto& [ptr, sample] : live_allocs) {
        if (sample) {
            sample->sample.export_sample();
        }
    }
}

/* Public API */
bool
memalloc_heap_profiler_init_no_cpython(uint32_t sample_interval)
{
    if (!heap_profiler_t::instance) {
        heap_profiler_t::instance = new heap_profiler_t(sample_interval);
        return true;
    }
    return false;
}

void
memalloc_heap_profiler_deinit_no_cpython(void)
{
    heap_profiler_t* old_instance = heap_profiler_t::instance;
    heap_profiler_t::instance = nullptr;
    delete old_instance;
}

void
memalloc_heap_untrack_no_cpython(void* ptr)
{
    if (heap_profiler_t::instance) {
        heap_profiler_t::instance->untrack_no_cpython(ptr);
    }
}

void
memalloc_heap_export_no_cpython(void)
{
    if (heap_profiler_t::instance) {
        heap_profiler_t::instance->export_heap_no_cpython();
    }
}
