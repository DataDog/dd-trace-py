#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <frameobject.h>
#include <string_view>

#include "_pymacro.h"

#include "_memalloc_debug.h"
#include "_memalloc_reentrant.h"
#include "_memalloc_tb.h"

/* Helper function to convert PyUnicode object to string_view
 * Returns the string_view pointing to internal UTF-8 representation, or fallback if conversion fails
 * The pointer remains valid as long as the PyObject is alive */
static std::string_view
unicode_to_string_view(PyObject* unicode_obj, std::string_view fallback = "<unknown>")
{
    if (unicode_obj == NULL) {
        return fallback;
    }

    Py_ssize_t len;
    const char* ptr = PyUnicode_AsUTF8AndSize(unicode_obj, &len);
    if (ptr) {
        return std::string_view(ptr, len);
    }
    // PyUnicode_AsUTF8AndSize always sets an error on failure (TypeError if not a
    // unicode object, MemoryError if UTF-8 cache allocation fails). Clear it since
    // we're inside the allocator hook and must not leave stale errors for the caller.
    PyErr_Clear();
    return fallback;
}

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

/* Helper function to extract frame info from PyFrameObject and push to sample */
static void
push_pyframe_to_sample(Datadog::Sample& sample, PyFrameObject* frame)
{
    int lineno_val = PyFrame_GetLineNumber(frame);
    if (lineno_val < 0)
        lineno_val = 0;

    PyCodeObject* code = PyFrame_GetCode(frame);

    std::string_view name_sv = "<unknown>";
    std::string_view filename_sv = "<unknown>";

    if (code != NULL) {
        // Extract function name (use co_qualname for Python 3.11+ for better context)
#if defined(_PY311_AND_LATER)
        PyObject* name_obj = code->co_qualname ? code->co_qualname : code->co_name;
#else
        PyObject* name_obj = code->co_name;
#endif
        name_sv = unicode_to_string_view(name_obj);
        filename_sv = unicode_to_string_view(code->co_filename);
    }

    // Push frame to Sample (leaf to root order)
    // push_frame copies the strings immediately into its StringArena
    sample.push_frame(name_sv, filename_sv, 0, lineno_val);

    Py_XDECREF(code);
}

/* Helper function to collect frames from PyFrameObject chain and push to sample */
static void
push_stacktrace_to_sample_invokes_cpython(Datadog::Sample& sample)
{
    PyThreadState* tstate = PyThreadState_Get();
    if (tstate == NULL) {
        // Push a placeholder frame when thread state is unavailable
        sample.push_frame("<no thread state>", "<unknown>", 0, 0);
        return;
    }

    PyFrameObject* pyframe = PyThreadState_GetFrame(tstate);

    if (pyframe == NULL) {
        // No Python frames available (e.g., during thread initialization/cleanup in "Dummy" threads).
        // This occurs in Python 3.10-3.12 but not in 3.13+ due to threading implementation changes.
        //
        // The previous implementation (before dd_wrapper Sample refactor) dropped these samples entirely
        // by returning NULL from traceback_new(). This new approach is strictly better: we still capture
        // allocation metrics and make it explicit in profiles that the stack wasn't available.
        //
        // TODO(profiling): Investigate if there's a way to capture C-level stack traces or other context
        // when Python frames aren't available during thread initialization/cleanup.
        sample.push_frame("<no Python frames>", "<unknown>", 0, 0);
        return;
    }

    for (PyFrameObject* frame = pyframe; frame != NULL;) {
        push_pyframe_to_sample(sample, frame);

        PyFrameObject* back = PyFrame_GetBack(frame);
        Py_DECREF(frame); // Release reference - pyframe from PyThreadState_GetFrame is a new reference, and back frames
                          // from GetBack are also new references
        frame = back;
        memalloc_debug_gil_release();
    }
}

void
traceback_t::init_sample_invokes_cpython(size_t size, size_t weighted_size)
{
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
