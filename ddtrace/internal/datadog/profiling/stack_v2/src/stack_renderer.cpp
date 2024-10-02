#include "stack_renderer.hpp"

#include "utf8_validate.hpp"

#include <Python.h>

using namespace Datadog;

std::string
get_exc_type_name(PyObject* exc_type)
{
    if (exc_type == nullptr) {
        return {};
    }
    PyObject* module_attr = PyObject_GetAttrString(exc_type, "__module__");
    if (module_attr == nullptr) {
        return {};
    }
    PyObject* name_attr = PyObject_GetAttrString(exc_type, "__name__");
    if (name_attr == nullptr) {
        Py_DECREF(module_attr);
        return {};
    }

    const char* module_str = PyUnicode_AsUTF8(module_attr);
    const char* name_str = PyUnicode_AsUTF8(name_attr);
    if (module_str == nullptr || name_str == nullptr) {
        Py_DECREF(module_attr);
        Py_DECREF(name_attr);
        return nullptr;
    }

    std::string result = std::string(module_str) + "." + std::string(name_str);

    Py_DECREF(module_attr);
    Py_DECREF(name_attr);

    return result;
}

void
push_exception_frames(Sample* sample, PyTracebackObject* tb)
{
    while (tb != nullptr) {
        PyFrameObject* frame = tb->tb_frame;
        if (frame != nullptr) {
#if PY_VERSION_HEX >= 0x030b0000
            // Since 3.11, members of PyFrameObject are removed from the public C API.
            PyCodeObject* code = PyFrame_GetCode(frame);
#else
            PyCoeObject* code = frame->f_code;
#endif
            if (code != nullptr) {
                PyObject* filename = code->co_filename;
                PyObject* name = code->co_name;
                if (filename != nullptr && name != nullptr) {
                    const char* filename_str = PyUnicode_AsUTF8(filename);
                    const char* name_str = PyUnicode_AsUTF8(name);
                    if (filename_str != nullptr && name_str != nullptr) {
                        ddup_push_frame(sample, name_str, filename_str, 0, code->co_firstlineno);
                    }
                }
#if PY_VERSION_HEX >= 0x030b0000
                Py_DECREF(code);
#endif
            }
        }
        tb = tb->tb_next;
    }
}

void
StackRenderer::maybe_collect_exception_sample(PyThreadState* tstate)
{
    _PyErr_StackItem* exc_info = _PyErr_GetTopmostException(tstate);

    if (exc_info == nullptr) {
        return;
    }

    PyObject* exc_type;
    PyObject* exc_traceback;

    // Python 3.12 changed the exception handling API to use a single PyObject* instead of a tuple.
#if PY_VERSION_HEX >= 0x030c0000
    // The following lines of code are equivalent to the following Python code:
    // exc_type = type(exc_info)

    exc_type = PyObject_Type(reinterpret_cast<PyObject*>(exc_info));
#else
    // The following lines of code are equivalent to the following Python code:
    // exc_type, _, _ = exc_info
    exc_type = exc_info->exc_type;

#endif

    if (exc_type == nullptr) {
        return;
    }

#if PY_VERSION_HEX >= 0x030c0000
    // exc_traceback = get_attr(exc_info, "__traceback__", None)
    exc_traceback = PyObject_GetAttrString(reinterpret_cast<PyObject*>(exc_info), "__traceback__");
#else
    // _, _, exctraceback = exc_info
    exc_traceback = exc_info->exc_traceback;
#endif

    if (exc_traceback == nullptr) {
#if PY_VERSION_HEX >= 0x030c0000
        // Not sure we really need to call Py_DECREF in this function at all,
        // because AFAIK tstate is already copied to this thread via
        // process_vm_readv() or equivalent system calls and we don't need to
        // worry about reference counting.
        Py_DECREF(exc_type); // PyObject_Type returns a new reference
        PyErr_Clear();       // Clear any error set by PyObject_GetAttrString
#endif
        return;
    }

    // Format exc_type as exc_type.__module__ + '.' + exc_type.__name__
    std::string exc_type_name = get_exc_type_name(exc_type);
    if (exc_type_name.empty()) {
#if PY_VERSION_HEX >= 0x030c0000
        Py_DECREF(exc_type);
#endif
        return;
    }

    // Now we have the exception type name, we can start building the exception sample
    Sample* exc_sample = ddup_start_sample();
    if (exc_sample == nullptr) {
        std::cerr << "Failed to create a sample.  Stack v2 sampler will be disabled." << std::endl;
#if PY_VERSION_HEX >= 0x030c0000
        Py_DECREF(exc_type);
#endif
        return;
    }
    ddup_push_monotonic_ns(exc_sample, thread_state.now_time_ns);
    ddup_push_threadinfo(exc_sample,
                         static_cast<int64_t>(thread_state.id),
                         static_cast<int64_t>(thread_state.native_id),
                         thread_state.name);
    ddup_push_exceptioninfo(exc_sample, exc_type_name, 1);
    push_exception_frames(exc_sample, reinterpret_cast<PyTracebackObject*>(exc_traceback));
    ddup_flush_sample(exc_sample);
    ddup_drop_sample(exc_sample);

#if PY_VERSION_HEX >= 0x030c0000
    Py_DECREF(exc_type);
#endif
}

void
StackRenderer::render_message(std::string_view msg)
{
    // This function is part of the necessary API, but it is unused by the Datadog profiler for now.
    (void)msg;
}

void
StackRenderer::render_thread_begin(PyThreadState* tstate,
                                   std::string_view name,
                                   microsecond_t wall_time_us,
                                   uintptr_t thread_id,
                                   unsigned long native_id)
{
    (void)tstate;
    static bool failed = false;
    if (failed) {
        return;
    }
    sample = ddup_start_sample();
    if (sample == nullptr) {
        std::cerr << "Failed to create a sample.  Stack v2 sampler will be disabled." << std::endl;
        failed = true;
        return;
    }

    // Get the current time in ns in a way compatible with python's time.monotonic_ns(), which is backed by
    // clock_gettime(CLOCK_MONOTONIC) on linux and mach_absolute_time() on macOS.
    // This is not the same as std::chrono::steady_clock, which is backed by clock_gettime(CLOCK_MONOTONIC_RAW)
    // (although this is underspecified in the standard)
    int64_t now_ns = 0;
    timespec ts;
    if (clock_gettime(CLOCK_MONOTONIC, &ts) == 0) {
        now_ns = static_cast<int64_t>(ts.tv_sec) * 1'000'000'000LL + static_cast<int64_t>(ts.tv_nsec);
        ddup_push_monotonic_ns(sample, now_ns);
    }

    // Save the thread information in case we observe a task on the thread
    thread_state.id = thread_id;
    thread_state.native_id = native_id;
    thread_state.name = std::string(name);
    thread_state.now_time_ns = now_ns;
    thread_state.wall_time_ns = 1000LL * wall_time_us;
    thread_state.cpu_time_ns = 0; // Walltime samples are guaranteed, but CPU times are not. Initialize to 0
                                  // since we don't know if we'll get a CPU time here.

    // Finalize the thread information we have
    ddup_push_threadinfo(sample, static_cast<int64_t>(thread_id), static_cast<int64_t>(native_id), name);
    ddup_push_walltime(sample, thread_state.wall_time_ns, 1);

    maybe_collect_exception_sample(tstate);
}

void
StackRenderer::render_task_begin(std::string_view name)
{
    static bool failed = false;
    if (failed) {
        return;
    }
    if (sample == nullptr) {
        // The very first task on a thread will already have a sample, since there's no way to deduce whether
        // a thread has tasks without checking, and checking before populating the sample would make the state
        // management very complicated.  The rest of the tasks will not have samples and will hit this code path.
        sample = ddup_start_sample();
        if (sample == nullptr) {
            std::cerr << "Failed to create a sample.  Stack v2 sampler will be disabled." << std::endl;
            failed = true;
            return;
        }

        // Add the thread context into the sample
        ddup_push_threadinfo(sample,
                             static_cast<int64_t>(thread_state.id),
                             static_cast<int64_t>(thread_state.native_id),
                             thread_state.name);
        ddup_push_walltime(sample, thread_state.wall_time_ns, 1);
        ddup_push_cputime(sample, thread_state.cpu_time_ns, 1); // initialized to 0, so possibly a no-op
        ddup_push_monotonic_ns(sample, thread_state.now_time_ns);
    }

    ddup_push_task_name(sample, name);
}

void
StackRenderer::render_stack_begin()
{
    // This function is part of the necessary API, but it is unused by the Datadog profiler for now.
}

void
StackRenderer::render_python_frame(std::string_view name, std::string_view file, uint64_t line)
{
    if (sample == nullptr) {
        std::cerr << "Received a new frame without sample storage.  Some profiling data has been lost." << std::endl;
        return;
    }

    // Normally, further utf-8 validation would be pointless here, but we may be reading data where the
    // string pointer was valid, but the string is actually garbage data at the exact time of the read.
    // This is rare, but blowing some cycles on early validation allows the sample to be retained by
    // libdatadog, so we can evaluate the actual impact of this scenario in live scenarios.
    static const std::string_view invalid = "<invalid_utf8>";
    if (!utf8_check_is_valid(name.data(), name.size())) {
        name = invalid;
    }
    if (!utf8_check_is_valid(file.data(), file.size())) {
        file = invalid;
    }
    ddup_push_frame(sample, name, file, 0, line);
}

void
StackRenderer::render_native_frame(std::string_view name, std::string_view file, uint64_t line)
{
    // This function is part of the necessary API, but it is unused by the Datadog profiler for now.
    (void)name;
    (void)file;
    (void)line;
}

void
StackRenderer::render_cpu_time(microsecond_t cpu_time_us)
{
    if (sample == nullptr) {
        std::cerr << "Received a CPU time without sample storage.  Some profiling data has been lost." << std::endl;
        return;
    }

    // TODO - it's absolutely false that thread-level CPU time is task time.  This needs to be normalized
    // to the task level, but for now just keep it because this is how the v1 sampler works
    thread_state.cpu_time_ns = 1000LL * cpu_time_us;
    ddup_push_cputime(sample, thread_state.cpu_time_ns, 1);
}

void
StackRenderer::render_stack_end()
{
    if (sample == nullptr) {
        std::cerr << "Ending a stack without any context.  Some profiling data has been lost." << std::endl;
        return;
    }

    ddup_flush_sample_v2(sample);
    ddup_drop_sample(sample);
    sample = nullptr;
}

bool
StackRenderer::is_valid()
{
    // In general, echion may need to check whether the extension has invalid state before calling into it,
    // but in this case it doesn't matter
    return true;
}
