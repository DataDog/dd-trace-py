#define PY_SSIZE_T_CLEAN

#include <string_view>

#include "_memalloc_frame.h"
#include "_memalloc_tb.h"
#include "python_helpers.hpp"

/* Helper function to get thread info using C-level APIs and push to sample.
 *
 * Uses only PyThread_get_thread_ident() and PyThread_get_thread_native_id(),
 * which are C-level calls that never re-enter the Python interpreter.
 * This is critical because this function is called from inside CPython's
 * PYMEM_DOMAIN_OBJ allocation path. Calling Python-level code (e.g.,
 * threading.current_thread()) from here can release the GIL via the eval
 * loop's periodic check, allowing other threads to observe partially-constructed
 * CPython internal state and causing crashes.
 *
 * Thread names are not available from C-level APIs; push_threadinfo falls back
 * to str(thread_id) when name is empty. The stack profiler provides thread names
 * via stack.register_thread() at safe points.
 */
static void
push_threadinfo_to_sample(Datadog::Sample& sample)
{
    int64_t thread_id = (int64_t)PyThread_get_thread_ident();
    if (thread_id == 0) {
        return;
    }

    int64_t thread_native_id = (int64_t)PyThread_get_thread_native_id();

    // Pass empty name; push_threadinfo will fall back to str(thread_id)
    sample.push_threadinfo(thread_id, thread_native_id, "");
}

/* Collect frames from the current thread's frame chain and push to sample.
 *
 * Uses direct internal CPython frame access instead of the public
 * PyThreadState_GetFrame() / PyFrame_GetBack() / PyFrame_GetCode() APIs.
 * Those public APIs return new references, meaning every frame visited would
 * require an INCREF + DECREF pair.  Inside an allocator hook those refcount
 * operations can themselves trigger re-entrant allocations, leading to
 * undefined behaviour.
 *
 * By reading frame pointers directly (borrowed references, no refcount change)
 * we eliminate that risk and reduce per-frame overhead.
 *
 * AIDEV-NOTE: Implementation mirrors ddtrace/internal/utils/_inspection.cpp
 * (PR #16430).  Keep in sync when updating CPython version support.
 */
static void
push_stacktrace_to_sample_invokes_cpython(Datadog::Sample& sample)
{
    PyThreadState* tstate = PyThreadState_Get();
    if (tstate == NULL) {
        sample.push_frame("<no thread state>", "<unknown>", 0, 0);
        return;
    }

    // Get the innermost frame as a borrowed pointer (no new reference created).
    void* current_frame = memalloc_get_frame_from_thread_state(tstate);

    if (current_frame == NULL) {
        // No Python frames available (e.g., during thread init/cleanup in "Dummy" threads).
        // This occurs in Python 3.10-3.12 but not in 3.13+ due to threading changes.
        sample.push_frame("<no Python frames>", "<unknown>", 0, 0);
        return;
    }

    // Walk the frame chain.  All pointers are borrowed — no INCREF/DECREF needed.
    for (void* frame = current_frame; frame != NULL; frame = memalloc_get_previous_frame(frame)) {
        if (memalloc_should_skip_frame(frame)) {
            continue;
        }

        // Borrowed reference — do NOT decref.
        PyCodeObject* code = memalloc_get_code_from_frame(frame);
        if (code == NULL) {
            break;
        }

        int lasti = memalloc_get_lasti_from_frame(frame);
        int lineno = memalloc_get_lineno_from_code(code, lasti);

        // co_filename and co_qualname/co_name are borrowed string objects.
        // PyUnicode_AsUTF8AndSize reads their UTF-8 cache and does not allocate
        // when the cache is already populated (which it normally is for code objects).
        static constexpr std::string_view unknown_sv = "<unknown>";

        std::string_view filename_sv = unknown_sv;
        if (code->co_filename != NULL) {
            Py_ssize_t len = 0;
            const char* ptr = PyUnicode_AsUTF8AndSize(code->co_filename, &len);
            if (ptr != NULL) {
                filename_sv = std::string_view(ptr, static_cast<size_t>(len));
            } else {
                PyErr_Clear();
            }
        }

        std::string_view name_sv = unknown_sv;
        PyObject* name_obj = memalloc_get_code_name(code);
        if (name_obj != NULL) {
            Py_ssize_t len = 0;
            const char* ptr = PyUnicode_AsUTF8AndSize(name_obj, &len);
            if (ptr != NULL) {
                name_sv = std::string_view(ptr, static_cast<size_t>(len));
            } else {
                PyErr_Clear();
            }
        }

        sample.push_frame(name_sv, filename_sv, 0, lineno);
    }
}

void
traceback_t::init_sample_invokes_cpython(size_t size, size_t weighted_size)
{
    // Preserve any raised C-level exception already in flight before touching
    // CPython C-API from inside the allocator hook.
    PythonErrorRestorer error_restorer;

    // Size 0 allocations are legal and we can hypothetically sample them,
    // e.g. if an allocation during sampling pushes us over the next sampling threshold,
    // but we can't sample it, so we sample the next allocation which happens to be 0
    // bytes. Defensively make sure size isn't 0.
    size_t adjusted_size = size > 0 ? size : 1;
    double scaled_count = ((double)weighted_size) / ((double)adjusted_size);
    size_t count = (size_t)scaled_count;

    // Push allocation info to sample
    // Note: profile_state is initialized in memalloc_start() before any traceback_t objects are created
    sample.push_alloc(weighted_size, count);

    // Get thread id and native_id using C-level APIs and push to sample
    push_threadinfo_to_sample(sample);

    // Collect frames from the Python frame chain and push to Sample
    // Note: Sample.push_frame() automatically enforces the max_nframe limit and tracks dropped frames.
    push_stacktrace_to_sample_invokes_cpython(sample);
}

// AIDEV-NOTE: Constructor invokes CPython APIs via init_sample_invokes_cpython()
traceback_t::traceback_t(size_t size, size_t weighted_size, uint16_t max_nframe)
  : sample(static_cast<Datadog::SampleType>(Datadog::SampleType::Allocation | Datadog::SampleType::Heap), max_nframe)
{
    // Validate Sample object is in a valid state before use
    if (max_nframe == 0) {
        // Should not happen, but defensive check
        return;
    }

    init_sample_invokes_cpython(size, weighted_size);
}
