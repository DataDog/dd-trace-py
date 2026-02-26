#include <string_view>

#include "_memalloc_frame.h"
#include "_memalloc_tb.h"

/* Extract a UTF-8 string_view from a Python unicode object without any
 * CPython API calls that could allocate, free, or touch error state.
 *
 * Uses only inline struct-field reads: PyUnicode_IS_COMPACT_ASCII,
 * PyUnicode_DATA, PyUnicode_GET_LENGTH. These are available on Python 3.3+.
 *
 * For non-ASCII strings (extremely rare for function names and filenames),
 * returns "<non-ascii>" rather than calling PyUnicode_AsUTF8AndSize which
 * can trigger allocation of a UTF-8 cache via PyObject_Malloc. */
static inline std::string_view
unicode_to_sv_no_alloc(PyObject* obj)
{
    if (obj == NULL || !PyUnicode_Check(obj)) {
        return "<unknown>";
    }
    if (PyUnicode_IS_COMPACT_ASCII(obj)) {
        return std::string_view((const char*)PyUnicode_DATA(obj), (size_t)PyUnicode_GET_LENGTH(obj));
    }
    return "<non-ascii>";
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

/* Collect frames from the current thread's frame chain and push to sample.
 *
 * Uses the helpers from _memalloc_frame.h for direct internal CPython frame
 * access instead of the public PyThreadState_GetFrame() / PyFrame_GetBack() /
 * PyFrame_GetCode() APIs. Those public APIs return new references, meaning
 * every frame visited would require an INCREF + DECREF pair. Inside an
 * allocator hook those refcount operations can themselves trigger re-entrant
 * allocations or frees, leading to undefined behaviour.
 *
 * By reading frame pointers directly (borrowed references, no refcount change)
 * we eliminate that risk and reduce per-frame overhead. */
static void
push_stacktrace_to_sample_no_decref(Datadog::Sample& sample)
{
    PyThreadState* tstate = PyThreadState_Get();
    if (tstate == NULL) {
        sample.push_frame("<no thread state>", "<unknown>", 0, 0);
        return;
    }

    void* current_frame = memalloc_get_frame_from_thread_state(tstate);
    if (current_frame == NULL) {
        sample.push_frame("<no Python frames>", "<unknown>", 0, 0);
        return;
    }

    for (void* frame = current_frame; frame != NULL; frame = memalloc_get_previous_frame(frame)) {
        if (memalloc_should_skip_frame(frame)) {
            continue;
        }

        PyCodeObject* code = memalloc_get_code_from_frame(frame);
        if (code == NULL) {
            continue;
        }

        std::string_view name_sv = unicode_to_sv_no_alloc(memalloc_get_code_name(code));
        std::string_view filename_sv = unicode_to_sv_no_alloc(code->co_filename);
        int line = memalloc_get_lineno(frame, code);

        sample.push_frame(name_sv, filename_sv, 0, line);
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

    sample.push_alloc(weighted_size, count);
    push_threadinfo_to_sample(sample);
    push_stacktrace_to_sample_no_decref(sample);
}

// AIDEV-NOTE: Constructor invokes CPython APIs via init_sample_invokes_cpython()
traceback_t::traceback_t(size_t size, size_t weighted_size, uint16_t max_nframe)
  : sample(static_cast<Datadog::SampleType>(Datadog::SampleType::Allocation | Datadog::SampleType::Heap), max_nframe)
{
    if (max_nframe == 0) {
        return;
    }

    init_sample_invokes_cpython(size, weighted_size);
}
