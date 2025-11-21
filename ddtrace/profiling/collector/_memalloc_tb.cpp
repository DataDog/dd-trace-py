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

// Cached references to _threading module functions
static PyObject* threading_module = NULL;
static PyObject* get_thread_name_func = NULL;
static PyObject* get_thread_native_id_func = NULL;

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

    // Initialize threading function references
    // Note: MemoryCollector.start() ensures _threading is imported before calling
    // _memalloc.start(), so this should normally succeed. If it fails, we return false
    // and the error will be handled up the stack.
    if (threading_module == NULL) {
        // Import the _threading module
        PyObject* mod_path = PyUnicode_DecodeFSDefault("ddtrace.profiling._threading");
        threading_module = PyImport_Import(mod_path);
        Py_XDECREF(mod_path);
        if (threading_module == NULL) {
            // Error is already set by PyImport_Import
            return false;
        }

        // Get the get_thread_name function
        get_thread_name_func = PyObject_GetAttrString(threading_module, "get_thread_name");
        if (get_thread_name_func == NULL || !PyCallable_Check(get_thread_name_func)) {
            Py_XDECREF(threading_module);
            threading_module = NULL;
            PyErr_SetString(PyExc_RuntimeError, "Failed to get get_thread_name from ddtrace.profiling._threading");
            return false;
        }
        // INCREF since we're storing this in a static variable
        Py_INCREF(get_thread_name_func);

        // Get the get_thread_native_id function
        get_thread_native_id_func = PyObject_GetAttrString(threading_module, "get_thread_native_id");
        if (get_thread_native_id_func == NULL || !PyCallable_Check(get_thread_native_id_func)) {
            Py_XDECREF(threading_module);
            Py_DECREF(get_thread_name_func);
            threading_module = NULL;
            get_thread_name_func = NULL;
            PyErr_SetString(PyExc_RuntimeError, "Failed to get get_thread_native_id from ddtrace.profiling._threading");
            return false;
        }
        // INCREF since we're storing this in a static variable
        Py_INCREF(get_thread_native_id_func);
    }

    return true;
}

void
traceback_t::deinit()
{
    // Clean up DDFrame class reference
    if (ddframe_class) {
        Py_DECREF(ddframe_class);
        ddframe_class = NULL;
    }

    // Clean up threading function references
    if (get_thread_name_func) {
        Py_DECREF(get_thread_name_func);
        get_thread_name_func = NULL;
    }
    if (get_thread_native_id_func) {
        Py_DECREF(get_thread_native_id_func);
        get_thread_native_id_func = NULL;
    }
    if (threading_module) {
        Py_DECREF(threading_module);
        threading_module = NULL;
    }
}

/* Helper function to get thread native_id and name from Python's _threading module
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

    // If threading functions aren't initialized, this is an error condition
    // (should not happen if initialization succeeded)
    if (!get_thread_name_func || !get_thread_native_id_func) {
        // This should not happen - initialization should have failed if functions weren't available
        // Push empty thread info as fallback
        int64_t thread_native_id = 0;
        std::string thread_name("");
        sample.push_threadinfo(thread_id, thread_native_id, thread_name);
        return;
    }

    // Get thread native ID from Python
    int64_t thread_native_id = 0;
    PyObject* thread_id_obj = PyLong_FromUnsignedLong(thread_id);
    if (thread_id_obj != NULL) {
        PyObject* native_id_obj = PyObject_CallFunctionObjArgs(get_thread_native_id_func, thread_id_obj, NULL);
        Py_DECREF(thread_id_obj);
        if (native_id_obj != NULL) {
            if (PyLong_Check(native_id_obj)) {
                thread_native_id = PyLong_AsLongLong(native_id_obj);
            } else {
                // get_thread_native_id should return a PyLong, but handle gracefully if it doesn't
                // Use thread_id as fallback
                thread_native_id = (int64_t)thread_id;
            }
            Py_DECREF(native_id_obj);
        } else {
            // Call failed, clear any exception that was set
            PyErr_Clear();
            // Use thread_id as fallback (get_thread_native_id returns thread_id if thread not found)
            thread_native_id = (int64_t)thread_id;
        }
    }

    // Get thread name from Python
    std::string thread_name;
    PyObject* thread_id_obj2 = PyLong_FromUnsignedLong(thread_id);
    if (thread_id_obj2 != NULL) {
        PyObject* name_obj = PyObject_CallFunctionObjArgs(get_thread_name_func, thread_id_obj2, NULL);
        Py_DECREF(thread_id_obj2);
        if (name_obj != NULL) {
            // get_thread_name can return None if thread is not found
            if (name_obj != Py_None && PyUnicode_Check(name_obj)) {
                // Convert Unicode to UTF-8 bytes, similar to how we handle frame names
                PyObject* name_bytes = PyUnicode_AsUTF8String(name_obj);
                if (name_bytes != NULL) {
                    const char* name_ptr = PyBytes_AsString(name_bytes);
                    if (name_ptr != NULL) {
                        Py_ssize_t name_len = PyBytes_Size(name_bytes);
                        // Store the thread name in a std::string
                        thread_name = std::string(name_ptr, name_len);
                    }
                    Py_DECREF(name_bytes);
                } else {
                    // PyUnicode_AsUTF8String failed, clear any exception
                    PyErr_Clear();
                }
            }
            // Note: If name_obj is None or not a Unicode string, thread_name remains empty
            Py_DECREF(name_obj);
        } else {
            // Call failed, clear any exception that was set
            PyErr_Clear();
        }
    }
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
    sample.set_reverse_locations(true);
    for (PyFrameObject* frame = pyframe; frame != NULL;) {
        // TODO(dsn): add a truncated frame to the traceback if we exceed the max_nframe

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
