#include <array>
#include <string_view>

#include "_memalloc_frame.h"
#include "_memalloc_tb.h"
#include "_memalloc_vm_copy.h"

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

template<size_t N>
static inline std::string_view
remote_unicode_to_sv_no_alloc(PyObject* unicode_addr, std::array<char, N>& buffer)
{
    if (unicode_addr == NULL) {
        return "<unknown>";
    }

    PyASCIIObject remote_ascii{};
    if (memalloc_copy_type(unicode_addr, remote_ascii)) {
        return "<unreadable>";
    }

    if (!remote_ascii.state.compact || !remote_ascii.state.ascii) {
        return "<non-ascii>";
    }

    Py_ssize_t len = remote_ascii.length;
    if (len < 0 || static_cast<size_t>(len) >= N) {
        return "<too-long>";
    }

    if (len > 0) {
        const void* data_addr = reinterpret_cast<const char*>(unicode_addr) + sizeof(PyASCIIObject);
        if (copy_memory(pid, data_addr, len, buffer.data())) {
            return "<unreadable>";
        }
    }

    return std::string_view(buffer.data(), static_cast<size_t>(len));
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
push_stacktrace_to_sample_no_refcount(Datadog::Sample& sample, uint16_t max_nframe)
{
    PyThreadState* tstate = PyThreadState_Get();
    if (tstate == NULL) {
        sample.push_frame("<no thread state>", "<unknown>", 0, 0);
        return;
    }

    memalloc_frame_t* current_frame = memalloc_get_frame_from_thread_state(tstate);
    if (current_frame == NULL) {
        sample.push_frame("<no Python frames>", "<unknown>", 0, 0);
        return;
    }

    uint16_t pushed_frames = 0;
    size_t walked_frames = 0;
    for (memalloc_frame_t* frame = current_frame; frame != NULL; frame = memalloc_get_previous_frame(frame)) {
        // Safety cap on raw frame-chain traversal, independent of emitted
        // frames, so allocator-hook walking always stays finite.
        if (++walked_frames > TRACEBACK_MAX_WALKED_NFRAME) {
            sample.incr_dropped_frames();
            break;
        }

        // Once we've reached the frame cap, record that deeper frames were
        // omitted and stop before doing more line-number or filename work.
        if (pushed_frames >= max_nframe) {
            sample.incr_dropped_frames();
            break;
        }

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
        ++pushed_frames;
    }
}

/* Collect frames by copying CPython structs into local storage.
 * This path is used for non-OBJ domains where callbacks can run without the GIL,
 * so direct Python-structure dereferences are avoided. */
static void
push_stacktrace_to_sample_remote_copy(Datadog::Sample& sample, uint16_t max_nframe)
{
    PyThreadState* tstate_addr = memalloc_get_unchecked_tstate_no_gil();
    if (tstate_addr == NULL) {
        sample.push_frame("<no thread state>", "<unknown>", 0, 0);
        return;
    }

    PyThreadState tstate{};
    if (memalloc_copy_type(tstate_addr, tstate)) {
        sample.push_frame("<unreadable thread state>", "<unknown>", 0, 0);
        return;
    }

    memalloc_frame_t* current_frame = NULL;
#ifdef _PY313_AND_LATER
    current_frame = tstate.current_frame;
#elif defined(_PY311_AND_LATER)
    if (tstate.cframe != NULL) {
        _PyCFrame cframe{};
        if (memalloc_copy_type(tstate.cframe, cframe)) {
            sample.push_frame("<unreadable cframe>", "<unknown>", 0, 0);
            return;
        }
        current_frame = cframe.current_frame;
    }
#else
    current_frame = tstate.frame;
#endif // _PY313_AND_LATER

    if (current_frame == NULL) {
        sample.push_frame("<no Python frames>", "<unknown>", 0, 0);
        return;
    }

    uint16_t pushed_frames = 0;
    size_t walked_frames = 0;
    while (current_frame != NULL) {
        if (++walked_frames > TRACEBACK_MAX_WALKED_NFRAME) {
            sample.incr_dropped_frames();
            break;
        }
        if (pushed_frames >= max_nframe) {
            sample.incr_dropped_frames();
            break;
        }

        memalloc_frame_t frame{};
        if (memalloc_copy_type(current_frame, frame)) {
            sample.incr_dropped_frames();
            break;
        }

        PyCodeObject* code_addr = NULL;
#ifdef _PY314_AND_LATER
        code_addr = (PyCodeObject*)((uintptr_t)frame.f_executable.bits & ~(uintptr_t)7);
#elif defined(_PY313_AND_LATER)
        code_addr = (PyCodeObject*)frame.f_executable;
#elif defined(_PY311_AND_LATER)
        code_addr = frame.f_code;
#else
        code_addr = frame.f_code;
#endif // _PY314_AND_LATER

        if (code_addr != NULL) {
            PyCodeObject code{};
            int lineno = 0;
            std::array<char, 256> name_buf{};
            std::array<char, 512> filename_buf{};
            std::string_view name_sv = "<remote-python-frame>";
            std::string_view filename_sv = "<remote>";
            if (!memalloc_copy_type(code_addr, code)) {
                lineno = code.co_firstlineno;
                PyObject* code_name_addr = NULL;
#ifdef _PY311_AND_LATER
                code_name_addr = code.co_qualname ? code.co_qualname : code.co_name;
#else
                code_name_addr = code.co_name;
#endif
                name_sv = remote_unicode_to_sv_no_alloc(code_name_addr, name_buf);
                filename_sv = remote_unicode_to_sv_no_alloc(code.co_filename, filename_buf);
            }
            sample.push_frame(name_sv, filename_sv, 0, lineno);
            ++pushed_frames;
        }

#ifdef _PY311_AND_LATER
        current_frame = frame.previous;
#else
        current_frame = frame.f_back;
#endif // _PY311_AND_LATER
    }
}

void
traceback_t::init_sample(size_t size, size_t weighted_size, uint16_t max_nframe, PyMemAllocatorDomain domain)
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
    if (domain == PYMEM_DOMAIN_OBJ) {
        push_stacktrace_to_sample_no_refcount(sample, max_nframe);
    } else {
        push_stacktrace_to_sample_remote_copy(sample, max_nframe);
    }
}

// AIDEV-NOTE: Constructor calls init_sample() which reads CPython structs directly
traceback_t::traceback_t(size_t size, size_t weighted_size, uint16_t max_nframe, PyMemAllocatorDomain domain)
  : sample(static_cast<Datadog::SampleType>(Datadog::SampleType::Allocation | Datadog::SampleType::Heap), max_nframe)
{
    if (max_nframe == 0) {
        return;
    }

    init_sample(size, weighted_size, max_nframe, domain);
}
