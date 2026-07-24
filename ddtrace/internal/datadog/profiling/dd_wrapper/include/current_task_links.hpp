#pragma once

#include <cstddef>
#include <cstdint>
#include <string_view>

namespace Datadog {

// CurrentTaskLinks records, per OS thread, the user-space task (e.g. a gevent
// greenlet) currently running on that thread.
//
// It decouples the producer of task context (the gevent greenlet tracer,
// running under the GIL on each greenlet switch) from consumers that cannot
// safely call into Python -- in particular the memory allocator hook, which
// runs inside CPython's allocator and must not allocate or touch the Python C
// API. The allocator hook reads the current task purely from C++ state.
//
// Storage is thread-local, which makes this both correct and safe for the
// gevent case:
//   * Correctness: gevent multiplexes all greenlets of a hub onto a single OS
//     thread. The thread that performs a greenlet switch is exactly the thread
//     that subsequently allocates, so the per-thread slot the allocator hook
//     reads is the one the switch wrote.
//   * Safety: the name is stored in a fixed-size buffer and the id is a plain
//     scalar, so recording a task performs no heap allocation. That matters
//     because if a write allocated, the allocator hook could fire mid-write on
//     the same thread and observe a half-updated record. With no allocation
//     under the hood, a write and a same-thread sampled read can never
//     interleave, so no lock is needed.
//
// The storage lives in the shared dd_wrapper library so that every profiling
// extension that links it shares the same thread-local slot: the stack
// profiler writes to it and the memory profiler reads from it.
namespace CurrentTaskLinks {

// Maximum stored task-name length in bytes; longer names are truncated.
// Greenlet names are short in practice.
constexpr size_t k_max_task_name = 96;

// Record the task now running on the calling OS thread. The name is copied into
// fixed-size thread-local storage (truncated to k_max_task_name) and may be
// empty.
void
set_current_task(int64_t task_id, std::string_view task_name);

// Forget the task association for the calling OS thread.
void
clear_current_task();

// If a task is recorded for the calling OS thread, return true and write its id
// to *task_id and its name to *task_name (the view points at thread-local
// storage valid until the next set/clear on this thread). Otherwise return
// false and leave the outputs untouched.
bool
get_current_task(int64_t* task_id, std::string_view* task_name);

// Forget the calling thread's association after a fork. Only the forking thread
// survives in the child, and it is about to be re-initialized, so its inherited
// association is dropped to avoid mis-attributing the child's early
// allocations.
void
postfork_child();

} // namespace CurrentTaskLinks

} // namespace Datadog
