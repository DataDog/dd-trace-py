#pragma once

#include <Python.h>
#include "sample.hpp"

/* Shared stacktrace collection utilities for memory profiling
 * These functions are used by both allocation and heap profiling */

namespace memalloc_stacktrace {

/* Push thread info (id, native_id, name) to sample
 * NOTE: Invokes CPython APIs */
void push_threadinfo_to_sample(Datadog::Sample& sample);

/* Push Python stacktrace to sample
 * NOTE: Invokes CPython APIs which may release the GIL during frame collection */
void push_stacktrace_to_sample_invokes_cpython(Datadog::Sample& sample);

/* Initialize shared stacktrace module (threading references, etc.)
 * Returns true on success, false otherwise
 * NOTE: Invokes CPython APIs */
[[nodiscard]] bool init_invokes_cpython();

/* Deinitialize shared stacktrace module
 * NOTE: Invokes CPython APIs */
void deinit_invokes_cpython();

} // namespace memalloc_stacktrace
