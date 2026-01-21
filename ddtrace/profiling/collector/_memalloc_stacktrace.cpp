#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <frameobject.h>
#include <string>
#include <string_view>

#include "_pymacro.h"
#include "_memalloc_debug.h"
#include "_memalloc_reentrant.h"
#include "_memalloc_stacktrace.h"

namespace memalloc_stacktrace {

// Cached reference to threading module and current_thread function
static PyObject* threading_module = NULL;
static PyObject* threading_current_thread = NULL;

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

/* Helper function to convert PyUnicode object to string_view */
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

    // Push frame to Sample (leaf to root order)
    sample.push_frame(name_sv, filename_sv, 0, lineno_val);

#ifdef _PY39_AND_LATER
    Py_XDECREF(code);
#endif
}

bool
init_invokes_cpython()
{
    // Initialize threading module structure references
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
    }
    return true;
}

void
deinit_invokes_cpython()
{
    // Check if Python is finalizing
#ifdef _PY313_AND_LATER
    if (Py_IsFinalizing()) {
#else
    if (_Py_IsFinalizing()) {
#endif
        threading_current_thread = NULL;
        threading_module = NULL;
        return;
    }

    // Save exception state before cleanup
    PythonErrorRestorer error_restorer;

    PyObject* old_threading_current_thread = threading_current_thread;
    threading_current_thread = NULL;
    Py_XDECREF(old_threading_current_thread);

    PyObject* old_threading_module = threading_module;
    threading_module = NULL;
    Py_XDECREF(old_threading_module);
}

void
push_threadinfo_to_sample(Datadog::Sample& sample)
{
    PythonErrorRestorer error_restorer;

    if (threading_current_thread == NULL) {
        return;
    }

    PyObject* thread = PyObject_CallObject(threading_current_thread, NULL);
    if (thread == NULL) {
        PyErr_Clear();
        return;
    }

    int64_t thread_id = 0;
    PyObject* ident_obj = PyObject_GetAttrString(thread, "ident");
    if (ident_obj != NULL && ident_obj != Py_None && PyLong_Check(ident_obj)) {
        thread_id = PyLong_AsLongLong(ident_obj);
    } else {
        thread_id = (int64_t)PyThread_get_thread_ident();
    }
    Py_XDECREF(ident_obj);

    if (thread_id == 0) {
        Py_DECREF(thread);
        return;
    }

    int64_t thread_native_id = thread_id;

    PyObject* name_obj = PyObject_GetAttrString(thread, "name");
    std::string_view thread_name = "";
    if (name_obj && name_obj != Py_None && PyUnicode_Check(name_obj)) {
        thread_name = unicode_to_string_view(name_obj, "");
    }

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

    sample.push_threadinfo(thread_id, thread_native_id, thread_name);

    Py_XDECREF(name_obj);
}

void
push_stacktrace_to_sample_invokes_cpython(Datadog::Sample& sample)
{
    PyThreadState* tstate = PyThreadState_Get();
    if (tstate == NULL) {
        sample.push_frame("<no thread state>", "<unknown>", 0, 0);
        return;
    }

#ifdef _PY39_AND_LATER
    PyFrameObject* pyframe = PyThreadState_GetFrame(tstate);
#else
    PyFrameObject* pyframe = tstate->frame;
#endif

    if (pyframe == NULL) {
        sample.push_frame("<no Python frames>", "<unknown>", 0, 0);
        return;
    }

    for (PyFrameObject* frame = pyframe; frame != NULL;) {
        push_pyframe_to_sample(sample, frame);

#ifdef _PY39_AND_LATER
        PyFrameObject* back = PyFrame_GetBack(frame);
        Py_DECREF(frame);
        frame = back;
#else
        frame = frame->f_back;
#endif
        memalloc_debug_gil_release();
    }
}

} // namespace memalloc_stacktrace
