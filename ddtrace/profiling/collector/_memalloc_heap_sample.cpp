#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include "_memalloc_heap_sample.h"
#include "_memalloc_stacktrace.h"

heap_sample_t::heap_sample_t(size_t size, uint16_t max_nframe)
  : sample(Datadog::SampleType::Heap, max_nframe)
{
    if (max_nframe == 0) {
        return;
    }

    init_sample_invokes_cpython(size);
}

void
heap_sample_t::init_sample_invokes_cpython(size_t size)
{
    // Push heap metrics only (use actual size, not weighted)
    sample.push_heap(size);

    // Collect thread info
    memalloc_stacktrace::push_threadinfo_to_sample(sample);

    // Collect stacktrace
    memalloc_stacktrace::push_stacktrace_to_sample_invokes_cpython(sample);
}

bool
heap_sample_t::init_invokes_cpython()
{
    return memalloc_stacktrace::init_invokes_cpython();
}

void
heap_sample_t::deinit_invokes_cpython()
{
    // Shared stacktrace module will be deinitialized separately
    // Nothing heap-specific to clean up
}
