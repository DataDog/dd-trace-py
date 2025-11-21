#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <frameobject.h>
#include <string>
#include <string_view>

#include "_memalloc_debug.h"
#include "_memalloc_reentrant.h"
#include "_memalloc_tb.h"
#include "_pymacro.h"

/* A string containing "<unknown>" just in case we can't store the real function
 * or file name. */
static PyObject* unknown_name = NULL;
/* A string containing "" */
static PyObject* empty_string = NULL;

static PyObject* ddframe_class = NULL;

// Cached references to threading module structures
static PyObject* threading_module = NULL;
static PyObject* threading_active = NULL;
static PyObject* threading_limbo = NULL;
static PyObject* periodic_threads_dict = NULL;

bool
traceback_t::init()
{
    if (unknown_name == NULL) {
        unknown_name = PyUnicode_FromString("<unknown>");
        if (unknown_name == NULL)
            return false;
        PyUnicode_InternInPlace(&unknown_name);
    }

    if (empty_string == NULL) {
        empty_string = PyUnicode_FromString("");
        if (empty_string == NULL)
            return false;
        PyUnicode_InternInPlace(&empty_string);
    }

    // Initialize DDFrame class reference
    if (ddframe_class == NULL) {
        // Import the module that contains the DDFrame class
        PyObject* mod_path = PyUnicode_DecodeFSDefault("ddtrace.profiling.event");
        PyObject* mod = PyImport_Import(mod_path);
        Py_XDECREF(mod_path);
        if (mod == NULL) {
            // Error is already set by PyImport_Import
            return false;
        }

        // Get the DDFrame class object
        ddframe_class = PyObject_GetAttrString(mod, "DDFrame");
        Py_DECREF(mod);

        // Basic sanity check that the object is the type of object we actually want
        if (ddframe_class == NULL || !PyCallable_Check(ddframe_class)) {
            PyErr_SetString(PyExc_RuntimeError, "Failed to get DDFrame from ddtrace.profiling.event");
            return false;
        }
    }

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

        // Get threading._active dictionary
        threading_active = PyObject_GetAttrString(threading_module, "_active");
        if (threading_active == NULL || !PyDict_Check(threading_active)) {
            Py_XDECREF(threading_module);
            threading_module = NULL;
            PyErr_SetString(PyExc_RuntimeError, "Failed to get threading._active");
            return false;
        }
        Py_INCREF(threading_active); // PyObject_GetAttrString returns new reference, but we want to keep it

        // Get threading._limbo dictionary (may not exist in all Python versions)
        threading_limbo = PyObject_GetAttrString(threading_module, "_limbo");
        if (threading_limbo != NULL && PyDict_Check(threading_limbo)) {
            Py_INCREF(threading_limbo); // Keep reference if it exists
        } else {
            Py_XDECREF(threading_limbo); // Clean up if it doesn't exist or isn't a dict
            threading_limbo = NULL;
            PyErr_Clear(); // Clear error if _limbo doesn't exist
        }

        // Get periodic_threads dictionary from ddtrace.internal._threads
        PyObject* threads_mod_path = PyUnicode_DecodeFSDefault("ddtrace.internal._threads");
        PyObject* threads_module = PyImport_Import(threads_mod_path);
        Py_XDECREF(threads_mod_path);
        if (threads_module != NULL) {
            periodic_threads_dict = PyObject_GetAttrString(threads_module, "periodic_threads");
            Py_DECREF(threads_module);
            if (periodic_threads_dict != NULL && PyDict_Check(periodic_threads_dict)) {
                Py_INCREF(periodic_threads_dict); // Keep reference
            } else {
                Py_XDECREF(periodic_threads_dict);
                periodic_threads_dict = NULL;
                PyErr_Clear(); // Clear error if periodic_threads doesn't exist
            }
        } else {
            PyErr_Clear(); // Clear error if module import fails (not critical)
        }
    }

    return true;
}

void
traceback_t::deinit()
{
    // Check if Python is finalizing. If so, skip cleanup to avoid segfaults.
    // During finalization, Python objects may be in an invalid state.
#if PY_VERSION_HEX >= 0x030d0000
    if (Py_IsFinalizing()) {
#else
    if (_Py_IsFinalizing()) {
#endif
        // Just clear the pointers without decrementing references
        ddframe_class = NULL;
        periodic_threads_dict = NULL;
        threading_limbo = NULL;
        threading_active = NULL;
        threading_module = NULL;
        return;
    }

    // Save exception state before cleanup, then restore it afterward.
    // This is important because deinit() may be called during exception handling
    // (e.g., when MemoryCollector.__exit__ is called with an exception), and
    // we need to preserve the exception for pytest.raises() and similar mechanisms.
    // We temporarily clear it during cleanup to avoid issues with Py_DECREF().
    PyObject* saved_exc_type = NULL;
    PyObject* saved_exc_value = NULL;
    PyObject* saved_exc_traceback = NULL;
    PyErr_Fetch(&saved_exc_type, &saved_exc_value, &saved_exc_traceback);

    // Use Py_XDECREF for all cleanup to safely handle NULL pointers.
    // During exception handling, objects may have been invalidated or set to NULL.
    PyObject* old_ddframe_class = ddframe_class;
    ddframe_class = NULL;
    Py_XDECREF(old_ddframe_class);

    PyObject* old_periodic_threads_dict = periodic_threads_dict;
    periodic_threads_dict = NULL;
    Py_XDECREF(old_periodic_threads_dict);

    PyObject* old_threading_limbo = threading_limbo;
    threading_limbo = NULL;
    Py_XDECREF(old_threading_limbo);

    PyObject* old_threading_active = threading_active;
    threading_active = NULL;
    Py_XDECREF(old_threading_active);

    PyObject* old_threading_module = threading_module;
    threading_module = NULL;
    Py_XDECREF(old_threading_module);

    // Restore the exception state if there was one
    if (saved_exc_type != NULL || saved_exc_value != NULL || saved_exc_traceback != NULL) {
        PyErr_Restore(saved_exc_type, saved_exc_value, saved_exc_traceback);
    }
}

/* Helper function to get thread object by ID from Python's threading dictionaries.
 * Checks periodic_threads, threading._active, and threading._limbo in that order.
 * Returns borrowed reference or NULL if not found.
 */
static PyObject*
get_thread_by_id(unsigned long thread_id)
{
    PyObject* thread_id_obj = PyLong_FromUnsignedLong(thread_id);
    if (thread_id_obj == NULL) {
        PyErr_Clear();
        return NULL;
    }

    PyObject* thread = NULL;

    // First check periodic_threads dictionary
    if (periodic_threads_dict != NULL) {
        thread = PyDict_GetItem(periodic_threads_dict, thread_id_obj);
        if (thread != NULL) {
            Py_DECREF(thread_id_obj);
            return thread; // Borrowed reference
        }
    }

    // Then check threading._active
    if (threading_active != NULL) {
        thread = PyDict_GetItem(threading_active, thread_id_obj);
        if (thread != NULL) {
            Py_DECREF(thread_id_obj);
            return thread; // Borrowed reference
        }
    }

    // Finally check threading._limbo
    if (threading_limbo != NULL) {
        thread = PyDict_GetItem(threading_limbo, thread_id_obj);
        if (thread != NULL) {
            Py_DECREF(thread_id_obj);
            return thread; // Borrowed reference
        }
    }

    Py_DECREF(thread_id_obj);
    return NULL;
}

/* Helper function to get thread native_id and name from Python's threading module
 * and push threadinfo to the sample.
 *
 * NOTE: This is called during traceback construction, which happens during allocation
 * tracking. We're already inside a reentrancy guard and GC is disabled, so it's safe
 * to call Python functions here (similar to how we call PyUnicode_AsUTF8String for frames).
 */
static void
push_threadinfo_to_sample(Datadog::Sample& sample, unsigned long thread_id)
{
    // If we don't have a valid thread_id, don't push anything
    if (thread_id == 0) {
        return;
    }

    // Default values
    int64_t thread_native_id = (int64_t)thread_id; // Fallback to thread_id
    std::string thread_name;

    PyObject* thread_id_obj = PyLong_FromUnsignedLong(thread_id);
    if (thread_id_obj == NULL) {
        PyErr_Clear();
        sample.push_threadinfo(thread_id, thread_native_id, thread_name);
        return;
    }

    PyObject* thread = NULL;

    // First try to get thread name from periodic_threads (if available)
    if (periodic_threads_dict != NULL) {
        PyObject* periodic_thread = PyDict_GetItem(periodic_threads_dict, thread_id_obj);
        if (periodic_thread != NULL) {
            // Get name attribute from periodic_thread
            PyObject* name_obj = PyObject_GetAttrString(periodic_thread, "name");
            if (name_obj != NULL && name_obj != Py_None && PyUnicode_Check(name_obj)) {
                PyObject* name_bytes = PyUnicode_AsUTF8String(name_obj);
                Py_DECREF(name_obj);
                if (name_bytes != NULL) {
                    const char* name_ptr = PyBytes_AsString(name_bytes);
                    if (name_ptr != NULL) {
                        Py_ssize_t name_len = PyBytes_Size(name_bytes);
                        thread_name = std::string(name_ptr, name_len);
                    }
                    Py_DECREF(name_bytes);
                } else {
                    PyErr_Clear();
                }
            } else {
                if (name_obj != NULL) {
                    Py_DECREF(name_obj);
                }
            }
        }
    }

    // Get thread from threading module (checks _active and _limbo)
    // This will also find periodic_threads if they're in threading._active
    thread = get_thread_by_id(thread_id);

    // If we have a thread object, get name and native_id
    if (thread != NULL) {
        // Get thread.name attribute (if we didn't already get it from periodic_threads)
        if (thread_name.empty()) {
            PyObject* name_obj = PyObject_GetAttrString(thread, "name");
            if (name_obj != NULL && name_obj != Py_None && PyUnicode_Check(name_obj)) {
                PyObject* name_bytes = PyUnicode_AsUTF8String(name_obj);
                Py_DECREF(name_obj);
                if (name_bytes != NULL) {
                    const char* name_ptr = PyBytes_AsString(name_bytes);
                    if (name_ptr != NULL) {
                        Py_ssize_t name_len = PyBytes_Size(name_bytes);
                        thread_name = std::string(name_ptr, name_len);
                    }
                    Py_DECREF(name_bytes);
                } else {
                    PyErr_Clear();
                }
            } else {
                if (name_obj != NULL) {
                    Py_DECREF(name_obj);
                }
            }
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
    }

    Py_DECREF(thread_id_obj);

    // Push threadinfo to sample with all data
    sample.push_threadinfo(thread_id, thread_native_id, thread_name);
}

traceback_t::traceback_t(void* ptr,
                         size_t size,
                         PyMemAllocatorDomain domain,
                         size_t weighted_size,
                         PyFrameObject* pyframe,
                         uint16_t max_nframe)
  : ptr(ptr)
  , size(weighted_size)
  , domain(domain)
  , thread_id(PyThread_get_thread_ident())
  , reported(false)
  , count(0)
  , sample(static_cast<Datadog::SampleType>(Datadog::SampleType::Allocation | Datadog::SampleType::Heap), max_nframe)
{
    // Size 0 allocations are legal and we can hypothetically sample them,
    // e.g. if an allocation during sampling pushes us over the next sampling threshold,
    // but we can't sample it, so we sample the next allocation which happens to be 0
    // bytes. Defensively make sure size isn't 0.
    size_t adjusted_size = size > 0 ? size : 1;
    double scaled_count = ((double)weighted_size) / ((double)adjusted_size);
    count = (size_t)scaled_count;

    // Validate Sample object is in a valid state before use
    if (max_nframe == 0) {
        // Should not happen, but defensive check
        return;
    }

    // Push allocation info to sample
    // Note: profile_state is initialized in memalloc_start() before any traceback_t objects are created
    sample.push_alloc(weighted_size, count);
    // Push heap info - use actual size (not weighted) for heap tracking
    // TODO(dsn): figure out if this actually makes sense, or if we should use the weighted size
    sample.push_heap(size);

    // Get thread native_id and name from Python's _threading module and push to sample
    push_threadinfo_to_sample(sample, thread_id);

    // Collect frames from the Python frame chain and push to Sample
    // We push frames as we collect them (root to leaf order).
    // Note: Sample.push_frame() comment says it "Assumes frames are pushed in leaf-order",
    // but we push root-to-leaf. Set reverse_locations so the sample will be reversed when exported.
    // Note: Sample.push_frame() automatically enforces the max_nframe limit and tracks dropped frames.
    sample.set_reverse_locations(true);
    for (PyFrameObject* frame = pyframe; frame != NULL;) {
        // Extract frame info for Sample
        int lineno_val = PyFrame_GetLineNumber(frame);
        if (lineno_val < 0)
            lineno_val = 0;

#ifdef _PY39_AND_LATER
        PyCodeObject* code = PyFrame_GetCode(frame);
#else
        PyCodeObject* code = frame->f_code;
#endif

        // Extract frame info for Sample
        // Create string_views directly from bytes buffers, push_frame copies them immediately,
        // then we can safely DECREF the bytes objects
        std::string_view name_sv = "<unknown>";
        std::string_view filename_sv = "<unknown>";

        PyObject* name_bytes = nullptr;
        PyObject* filename_bytes = nullptr;

        if (code != NULL) {
            if (code->co_name) {
                name_bytes = PyUnicode_AsUTF8String(code->co_name);
                if (name_bytes) {
                    const char* name_ptr = PyBytes_AsString(name_bytes);
                    if (name_ptr) {
                        Py_ssize_t name_len = PyBytes_Size(name_bytes);
                        name_sv = std::string_view(name_ptr, name_len);
                    }
                }
            }

            if (code->co_filename) {
                filename_bytes = PyUnicode_AsUTF8String(code->co_filename);
                if (filename_bytes) {
                    const char* filename_ptr = PyBytes_AsString(filename_bytes);
                    if (filename_ptr) {
                        Py_ssize_t filename_len = PyBytes_Size(filename_bytes);
                        filename_sv = std::string_view(filename_ptr, filename_len);
                    }
                }
            }
        }

        // Push frame to Sample (root to leaf order)
        // push_frame copies the strings immediately into its StringArena, so it's safe to
        // DECREF the bytes objects after this call
        sample.push_frame(name_sv, filename_sv, 0, lineno_val);

        // Now safe to release the bytes objects since push_frame has copied the strings
        Py_XDECREF(name_bytes);
        Py_XDECREF(filename_bytes);

#ifdef _PY39_AND_LATER
        Py_XDECREF(code);
#endif

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

traceback_t::~traceback_t()
{
    // Sample object is a member variable and will be automatically destroyed
    // No explicit cleanup needed - its destructor will handle internal cleanup
}

traceback_t*
traceback_t::get_traceback(uint16_t max_nframe,
                           void* ptr,
                           size_t size,
                           PyMemAllocatorDomain domain,
                           size_t weighted_size)
{
    PyThreadState* tstate = PyThreadState_Get();

    if (tstate == NULL)
        return NULL;

#ifdef _PY39_AND_LATER
    PyFrameObject* pyframe = PyThreadState_GetFrame(tstate);
#else
    PyFrameObject* pyframe = tstate->frame;
#endif

    if (pyframe == NULL)
        return NULL;

    return new traceback_t(ptr, size, domain, weighted_size, pyframe, max_nframe);
}
