#include <cassert>
#include <cmath>
#include <cstdlib>

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include "_memalloc_allocation.h"
#include "_memalloc_allocation_profiler.h"
#include "_memalloc_reentrant.h"

// Static member definition
allocation_profiler_t* allocation_profiler_t::instance = nullptr;

// Static helper function
uint32_t
allocation_profiler_t::next_sample_interval_no_cpython(uint32_t sample_interval)
{
    /* Draw a sampling target from an exponential distribution with average sample_interval */
    double q = (double)rand() / ((double)RAND_MAX + 1);
    double log_val = log2(q);
    return (uint32_t)(log_val * (-log(2) * (sample_interval + 1)));
}

// Constructor
allocation_profiler_t::allocation_profiler_t(uint32_t sample_interval_val)
  : sample_interval(sample_interval_val)
  , current_sample_interval(next_sample_interval_no_cpython(sample_interval_val))
  , allocated_memory(0)
{
}

bool
allocation_profiler_t::should_sample_no_cpython(size_t size, uint64_t* allocated_memory_val)
{
    allocated_memory += size;
    *allocated_memory_val = allocated_memory;

    /* Check if we have enough bytes allocated to trigger a sample */
    if (allocated_memory < current_sample_interval) {
        return false;
    }

    return true;
}

void
allocation_profiler_t::track_allocation_invokes_cpython(size_t size, uint64_t allocated_memory_val, uint16_t max_nframe)
{
    /* Avoid loops */
    memalloc_reentrant_guard_t guard;
    if (!guard) {
        return;
    }

    /* Create allocation sample - this invokes CPython APIs */
    allocation_sample_t sample(size, allocated_memory_val, max_nframe);

    /* Export immediately - fire and forget! No tracking needed */
    sample.sample.export_sample();

    /* Get ready for the next sample */
    allocated_memory = 0;
    current_sample_interval = next_sample_interval_no_cpython(sample_interval);
}

/* Public API */
bool
memalloc_allocation_profiler_init_no_cpython(uint32_t sample_interval)
{
    if (!allocation_profiler_t::instance) {
        allocation_profiler_t::instance = new allocation_profiler_t(sample_interval);
        return true;
    }
    return false;
}

void
memalloc_allocation_profiler_deinit_no_cpython(void)
{
    allocation_profiler_t* old_instance = allocation_profiler_t::instance;
    allocation_profiler_t::instance = nullptr;
    delete old_instance;
}
