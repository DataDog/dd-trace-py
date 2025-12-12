#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <frameobject.h>
#include <string>
#include <string_view>

#include "_pymacro.h"

#include "_memalloc_debug.h"
#include "_memalloc_reentrant.h"
#include "_memalloc_tb.h"

// Cached reference to threading module and current_thread function
static PyObject* threading_module = NULL;
static PyObject* threading_current_thread = NULL;

bool
traceback_t::init_invokes_cpython()
{
    // Initialize threading module structure references
    // Note: MemoryCollector.start() ensures _threading is imported before calling
    // _memalloc.start(), so this should normally succeed. If it fails, we return false
    // and the error will be handled up the stack.
    if (threading_module == NULL) {
        // Import the threading module (or use ddtrace's unpatched version)
        PyObject* sys_modules = PyImport_GetModuleDict();
        if (sys_modules == NULL) {
            PyErr_SetString(PyExc_RuntimeError, "Failed to get sys.modules");
            return false;
        }

        // Try to get threading module from sys.modules (don't force import)
        PyObject* threading_mod_name = PyUnicode_FromString("threading");
        if (threading_mod_name == NULL) {
            return false;
        }
        threading_module = PyDict_GetItem(sys_modules, threading_mod_name);
        Py_DECREF(threading_mod_name);

        // If threading not in sys.modules, try ddtrace.internal._unpatched._threading
        if (threading_module == NULL) {
            PyObject* mod_path = PyUnicode_DecodeFSDefault("ddtrace.internal._unpatched._threading");
            threading_module = PyImport_Import(mod_path);
            Py_XDECREF(mod_path);
            if (threading_module == NULL) {
                // Error is already set by PyImport_Import
                return false;
            }
        } else {
            Py_INCREF(threading_module); // PyDict_GetItem returns borrowed reference
        }

        // Get threading.current_thread function
        threading_current_thread = PyObject_GetAttrString(threading_module, "current_thread");
        if (threading_current_thread == NULL || !PyCallable_Check(threading_current_thread)) {
            Py_XDECREF(threading_module);
            threading_module = NULL;
            Py_XDECREF(threading_current_thread);
            threading_current_thread = NULL;
            PyErr_SetString(PyExc_RuntimeError, "Failed to get threading.current_thread");
            return false;
        }
        // PyObject_GetAttrString returns new reference, keep it
    }
    return true;
}

/* RAII helper to save and restore Python error state */
class PythonErrorRestorer
{
  public:
    PythonErrorRestorer()
    {
#ifdef _PY312_AND_LATER
        // Python 3.12+: Use the new API that returns a single exception object
        saved_exception = PyErr_GetRaisedException();
#else
        // Python < 3.12: Use the old API with separate type, value, traceback
        PyErr_Fetch(&saved_exc_type, &saved_exc_value, &saved_exc_traceback);
#endif
    }

    ~PythonErrorRestorer()
    {
#ifdef _PY312_AND_LATER
        // Python 3.12+: Restore using the new API if there was an exception
        if (saved_exception != NULL) {
            PyErr_SetRaisedException(saved_exception);
        }
#else
        // Python < 3.12: Restore using the old API if there was an exception
        if (saved_exc_type != NULL || saved_exc_value != NULL || saved_exc_traceback != NULL) {
            PyErr_Restore(saved_exc_type, saved_exc_value, saved_exc_traceback);
        }
#endif
    }

    // Non-copyable, non-movable
    PythonErrorRestorer(const PythonErrorRestorer&) = delete;
    PythonErrorRestorer& operator=(const PythonErrorRestorer&) = delete;
    PythonErrorRestorer(PythonErrorRestorer&&) = delete;
    PythonErrorRestorer& operator=(PythonErrorRestorer&&) = delete;

  private:
#ifdef _PY312_AND_LATER
    PyObject* saved_exception;
#else
    PyObject* saved_exc_type;
    PyObject* saved_exc_value;
    PyObject* saved_exc_traceback;
#endif
};

void
traceback_t::deinit_invokes_cpython()
{
    // Check if Python is finalizing. If so, skip cleanup to avoid segfaults.
    // During finalization, Python objects may be in an invalid state.
#ifdef _PY313_AND_LATER
    if (Py_IsFinalizing()) {
#else
    if (_Py_IsFinalizing()) {
#endif
        // Just clear the pointers without decrementing references
        threading_current_thread = NULL;
        threading_module = NULL;
        return;
    }

    // Save exception state before cleanup, then restore it afterward.
    // This is important because deinit() may be called during exception handling
    // (e.g., when MemoryCollector.__exit__ is called with an exception), and
    // we need to preserve the exception for pytest.raises() and similar mechanisms.
    // We temporarily clear it during cleanup to avoid issues with Py_DECREF().
    PythonErrorRestorer error_restorer;

    // Use Py_XDECREF for all cleanup to safely handle NULL pointers.
    // During exception handling, objects may have been invalidated or set to NULL.
    PyObject* old_threading_current_thread = threading_current_thread;
    threading_current_thread = NULL;
    Py_XDECREF(old_threading_current_thread);

    PyObject* old_threading_module = threading_module;
    threading_module = NULL;
    Py_XDECREF(old_threading_module);

    // Error will be restored automatically by error_restorer destructor
}

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
    return fallback;
}

/* Helper function to get thread native_id and name from Python's threading module
 * and push threadinfo to the sample.
 *
 * NOTE: This is called during traceback construction, which happens during allocation
 * tracking. We're already inside a reentrancy guard and GC is disabled, so it's safe
 * to call Python functions here (similar to how we call PyUnicode_AsUTF8AndSize for frames).
 */
static void
push_threadinfo_to_sample(Datadog::Sample& sample)
{
    // Save any existing error state to avoid masking errors
    PythonErrorRestorer error_restorer;

    // Use threading.current_thread() to get the current thread object
    if (threading_current_thread == NULL) {
        // threading.current_thread not available, don't push anything
        // Error will be restored automatically by error_restorer destructor
        return;
    }

    // Call threading.current_thread() - equivalent to threading.current_thread()
    PyObject* thread = PyObject_CallObject(threading_current_thread, NULL);
    if (thread == NULL) {
        PyErr_Clear();
        // Failed to get thread, don't push anything
        // Error will be restored automatically by error_restorer destructor
        return;
    }

    // Get thread.ident attribute (thread ID)
    // If thread.ident is None (can happen before thread starts), fall back to PyThread_get_thread_ident()
    int64_t thread_id = 0;
    PyObject* ident_obj = PyObject_GetAttrString(thread, "ident");
    if (ident_obj != NULL && ident_obj != Py_None && PyLong_Check(ident_obj)) {
        thread_id = PyLong_AsLongLong(ident_obj);
    } else {
        // Fallback to PyThread_get_thread_ident() if thread.ident is None
        thread_id = (int64_t)PyThread_get_thread_ident();
    }
    Py_XDECREF(ident_obj);

    // If we still don't have a valid thread_id, don't push anything
    if (thread_id == 0) {
        Py_DECREF(thread);
        return;
    }
    // Initialize native_id to thread_id as fallback; will be overwritten below if thread.native_id is available
    int64_t thread_native_id = thread_id;

    // Get thread.name attribute and keep it alive until we use it
    PyObject* name_obj = PyObject_GetAttrString(thread, "name");
    std::string_view thread_name = "";
    if (name_obj && name_obj != Py_None && PyUnicode_Check(name_obj)) {
        thread_name = unicode_to_string_view(name_obj, "");
    }

    // Get thread.native_id attribute
    PyObject* native_id_obj = PyObject_GetAttrString(thread, "native_id");
    if (native_id_obj != NULL) {
        if (PyLong_Check(native_id_obj)) {
            thread_native_id = PyLong_AsLongLong(native_id_obj);
        }
        Py_DECREF(native_id_obj);
    } else {
        PyErr_Clear();
    }

    Py_DECREF(thread);

    // Push threadinfo to sample with all data (name_obj must still be alive here)
    sample.push_threadinfo(thread_id, thread_native_id, thread_name);

    // Now safe to release name_obj since push_threadinfo has copied the string
    Py_XDECREF(name_obj);

    // Error will be restored automatically by error_restorer destructor
}

/* Helper function to extract frame info from PyFrameObject and push to sample */
static void
push_pyframe_to_sample(Datadog::Sample& sample, PyFrameObject* frame)
{
    int lineno_val = PyFrame_GetLineNumber(frame);
    if (lineno_val < 0)
        lineno_val = 0;

#ifdef _PY39_AND_LATER
    PyCodeObject* code = PyFrame_GetCode(frame);
#else
    PyCodeObject* code = frame->f_code;
#endif

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

    // Push frame to Sample (root to leaf order)
    // push_frame copies the strings immediately into its StringArena
    sample.push_frame(name_sv, filename_sv, 0, lineno_val);

#ifdef _PY39_AND_LATER
    Py_XDECREF(code);
#endif
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

#ifdef _PY39_AND_LATER
    PyFrameObject* pyframe = PyThreadState_GetFrame(tstate);
#else
    PyFrameObject* pyframe = tstate->frame;
#endif

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

#ifdef _PY39_AND_LATER
        PyFrameObject* back = PyFrame_GetBack(frame);
        Py_DECREF(frame); // Release reference - pyframe from PyThreadState_GetFrame is a new reference, and back frames
                          // from GetBack are also new references
        frame = back;
#else
        frame = frame->f_back;
#endif
        memalloc_debug_gil_release();
    }
}

void
traceback_t::init_sample_invokes_cpython(size_t size, size_t weighted_size)
{
    // Save any existing error state to avoid masking errors during traceback construction/reset
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
    // Push heap info - use actual size (not weighted) for heap tracking
    // TODO(dsn): figure out if this actually makes sense, or if we should use the weighted size
    sample.push_heap(size);

    // Get thread native_id and name from Python's threading module and push to sample
    push_threadinfo_to_sample(sample);

    // Collect frames from the Python frame chain and push to Sample
    // We push frames as we collect them (root to leaf order).
    // Note: Sample.push_frame() comment says it "Assumes frames are pushed in leaf-order",
    // but we push root-to-leaf. Set reverse_locations so the sample will be reversed when exported.
    // Note: Sample.push_frame() automatically enforces the max_nframe limit and tracks dropped frames.
    sample.set_reverse_locations(true);
    push_stacktrace_to_sample_invokes_cpython(sample);
}

// AIDEV-NOTE: Constructor invokes CPython APIs via init_sample_invokes_cpython()
traceback_t::traceback_t(size_t size, size_t weighted_size, uint16_t max_nframe)
  : reported(false)
  , sample(static_cast<Datadog::SampleType>(Datadog::SampleType::Allocation | Datadog::SampleType::Heap), max_nframe)
{
    // Validate Sample object is in a valid state before use
    if (max_nframe == 0) {
        // Should not happen, but defensive check
        return;
    }

    init_sample_invokes_cpython(size, weighted_size);
}

void
traceback_t::reset_invokes_cpython(size_t size, size_t weighted_size)
{
    sample.clear_buffers();
    reported = false;
    init_sample_invokes_cpython(size, weighted_size);
}
