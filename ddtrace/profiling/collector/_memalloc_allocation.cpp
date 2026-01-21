#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include "_memalloc_allocation.h"
#include "_memalloc_stacktrace.h"

allocation_sample_t::allocation_sample_t(size_t size, size_t weighted_size, uint16_t max_nframe)
  : sample(Datadog::SampleType::Allocation, max_nframe)
{
    if (max_nframe == 0) {
        return;
    }

    init_sample_invokes_cpython(size, weighted_size);
}

void
allocation_sample_t::init_sample_invokes_cpython(size_t size, size_t weighted_size)
{
    // Defensively make sure size isn't 0
    size_t adjusted_size = size > 0 ? size : 1;
    double scaled_count = ((double)weighted_size) / ((double)adjusted_size);
    size_t count = (size_t)scaled_count;

    // Push allocation metrics only
    sample.push_alloc(weighted_size, count);

    // Collect thread info
    memalloc_stacktrace::push_threadinfo_to_sample(sample);

    // Collect stacktrace
    memalloc_stacktrace::push_stacktrace_to_sample_invokes_cpython(sample);
}

bool
allocation_sample_t::init_invokes_cpython()
{
    return memalloc_stacktrace::init_invokes_cpython();
}

void
allocation_sample_t::deinit_invokes_cpython()
{
    // Shared stacktrace module will be deinitialized separately
    // Nothing allocation-specific to clean up
}
