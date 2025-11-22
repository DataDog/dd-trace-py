#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <frameobject.h>
#include <string>
#include <string_view>

#if PY_VERSION_HEX >= 0x030b0000 && PY_VERSION_HEX < 0x030d0000
// We can't include internal/pycore_frame.h because it requires Py_BUILD_CORE
// Instead, we define minimal structures matching CPython's internal layout
// These structures are internal to CPython but we need to access them
// to avoid allocations from PyFrameObject creation
// Note: Python 3.13+ has different internal structures, so we only use this for 3.11-3.12
extern "C"
{
    // Minimal structure definition matching _PyInterpreterFrame layout
    // Based on CPython's Include/internal/pycore_frame.h
    typedef struct _PyInterpreterFrame
    {
        PyObject* f_executable;
        void* prev_instr; // _Py_CODEUNIT* - pointer to instruction
        struct _PyInterpreterFrame* previous;
        // Note: There are other fields, but we only need these three
    } _PyInterpreterFrame;

    // Minimal structure definition matching _PyCFrame layout
    // Based on CPython's Include/internal/pycore_frame.h
    typedef struct _PyCFrame
    {
        struct _PyInterpreterFrame* current_frame;
        // Note: There are other fields, but we only need current_frame
    } _PyCFrame;

    // _Py_CODEUNIT is uint16_t in Python 3.11+
    typedef uint16_t _Py_CODEUNIT;
}
#endif

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

// Cached reference to threading module and current_thread function
static PyObject* threading_module = NULL;
static PyObject* threading_current_thread = NULL;

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
        PyErr_Fetch(&saved_exc_type, &saved_exc_value, &saved_exc_traceback);
        had_error = (saved_exc_type != NULL || saved_exc_value != NULL || saved_exc_traceback != NULL);
    }

    ~PythonErrorRestorer()
    {
        if (had_error) {
            PyErr_Restore(saved_exc_type, saved_exc_value, saved_exc_traceback);
        } else {
            // No error was present; safely decrement reference counts
            Py_XDECREF(saved_exc_type);
            Py_XDECREF(saved_exc_value);
            Py_XDECREF(saved_exc_traceback);
        }
    }

    // Non-copyable, non-movable
    PythonErrorRestorer(const PythonErrorRestorer&) = delete;
    PythonErrorRestorer& operator=(const PythonErrorRestorer&) = delete;
    PythonErrorRestorer(PythonErrorRestorer&&) = delete;
    PythonErrorRestorer& operator=(PythonErrorRestorer&&) = delete;

  private:
    PyObject* saved_exc_type;
    PyObject* saved_exc_value;
    PyObject* saved_exc_traceback;
    bool had_error;
};

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
    PyObject* old_ddframe_class = ddframe_class;
    ddframe_class = NULL;
    Py_XDECREF(old_ddframe_class);

    PyObject* old_threading_current_thread = threading_current_thread;
    threading_current_thread = NULL;
    Py_XDECREF(old_threading_current_thread);

    PyObject* old_threading_module = threading_module;
    threading_module = NULL;
    Py_XDECREF(old_threading_module);

    // Error will be restored automatically by error_restorer destructor
}

/* Helper function to get thread native_id and name from Python's threading module
 * and push threadinfo to the sample.
 *
 * NOTE: This is called during traceback construction, which happens during allocation
 * tracking. We're already inside a reentrancy guard and GC is disabled, so it's safe
 * to call Python functions here (similar to how we call PyUnicode_AsUTF8String for frames).
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
    std::string thread_name;

    // Get thread.name attribute
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
        Py_XDECREF(name_obj);
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

    // Push threadinfo to sample with all data
    sample.push_threadinfo(thread_id, thread_native_id, thread_name);

    // Error will be restored automatically by error_restorer destructor
}

/* Helper function to extract code object and line number from a PyFrameObject */
static void
extract_frame_info_from_pyframe(PyFrameObject* frame, PyCodeObject** code_out, int* lineno_out)
{
    *code_out = NULL;
    *lineno_out = 0;

    int lineno_val = PyFrame_GetLineNumber(frame);
    if (lineno_val < 0)
        lineno_val = 0;

#ifdef _PY39_AND_LATER
    PyCodeObject* code = PyFrame_GetCode(frame);
#else
    PyCodeObject* code = frame->f_code;
#endif

    *code_out = code;
    *lineno_out = lineno_val;
}

#if PY_VERSION_HEX >= 0x030b0000 && PY_VERSION_HEX < 0x030d0000
/* Helper function to extract code object and line number from a _PyInterpreterFrame
 * Only available for Python 3.11-3.12. Python 3.13+ uses different internal structures. */
static void
extract_frame_info_from_interpreter_frame(_PyInterpreterFrame* frame, PyCodeObject** code_out, int* lineno_out)
{
    *code_out = NULL;
    *lineno_out = 0;

    // Extract code object from interpreter frame
    // Python 3.11-3.12: f_executable is always a code object
    PyObject* f_executable = frame->f_executable;
    PyCodeObject* code = (PyCodeObject*)f_executable;

    if (code == NULL)
        return;

    // Calculate line number from interpreter frame
    // Use PyCode_Addr2Line with the frame's instruction pointer
    int lineno_val = 0;
    if (frame->prev_instr != NULL) {
        // Calculate bytecode offset from instruction pointer
        // frame->prev_instr is a pointer to _Py_CODEUNIT (uint16_t, 2 bytes per instruction in Python 3.11+)
        // We need to calculate the offset in code units
        // Python 3.11-3.12: co_code is directly accessible
        PyObject* code_bytes = code->co_code;
        Py_INCREF(code_bytes); // Borrowed reference, need to INCREF for consistency
        if (code_bytes != NULL) {
            const uint8_t* code_start = (const uint8_t*)PyBytes_AS_STRING(code_bytes);
            const _Py_CODEUNIT* instr_ptr = (const _Py_CODEUNIT*)frame->prev_instr;
            if (instr_ptr >= (const _Py_CODEUNIT*)code_start) {
                Py_ssize_t offset = instr_ptr - (const _Py_CODEUNIT*)code_start;
                lineno_val = PyCode_Addr2Line(code, offset);
            }
            Py_DECREF(code_bytes);
        }
    }
    if (lineno_val < 0)
        lineno_val = 0;

    *code_out = code;
    *lineno_out = lineno_val;
}
#endif

/* Helper function to push frame info to sample */
static void
push_frame_to_sample(Datadog::Sample& sample, PyCodeObject* code, int lineno_val)
{
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

    // Get thread native_id and name from Python's threading module and push to sample
    push_threadinfo_to_sample(sample);

    // Collect frames from the Python frame chain and push to Sample
    // We push frames as we collect them (root to leaf order).
    // Note: Sample.push_frame() comment says it "Assumes frames are pushed in leaf-order",
    // but we push root-to-leaf. Set reverse_locations so the sample will be reversed when exported.
    // Note: Sample.push_frame() automatically enforces the max_nframe limit and tracks dropped frames.
    sample.set_reverse_locations(true);
    for (PyFrameObject* frame = pyframe; frame != NULL;) {
        PyCodeObject* code = NULL;
        int lineno_val = 0;

        extract_frame_info_from_pyframe(frame, &code, &lineno_val);

        if (code != NULL) {
            push_frame_to_sample(sample, code, lineno_val);
        }

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

#if PY_VERSION_HEX >= 0x030b0000 && PY_VERSION_HEX < 0x030d0000
// Constructor for Python 3.11-3.12 - uses _PyInterpreterFrame directly to avoid allocations
// Note: Python 3.13+ has different internal structures, so we use PyFrameObject instead
traceback_t::traceback_t(void* ptr,
                         size_t size,
                         PyMemAllocatorDomain domain,
                         size_t weighted_size,
                         PyThreadState* tstate,
                         uint16_t max_nframe)
  : ptr(ptr)
  , size(weighted_size)
  , domain(domain)
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

    // Get thread native_id and name from Python's threading module and push to sample
    push_threadinfo_to_sample(sample);

    // Collect frames from the Python interpreter frame chain and push to Sample
    // We push frames as we collect them (root to leaf order).
    // Note: Sample.push_frame() comment says it "Assumes frames are pushed in leaf-order",
    // but we push root-to-leaf. Set reverse_locations so the sample will be reversed when exported.
    // Note: Sample.push_frame() automatically enforces the max_nframe limit and tracks dropped frames.
    sample.set_reverse_locations(true);

    // Access interpreter frames directly from tstate->cframe to avoid creating PyFrameObjects
    _PyCFrame* cframe = tstate->cframe;
    if (cframe == NULL)
        return;

    _PyInterpreterFrame* frame = cframe->current_frame;
    if (frame == NULL)
        return;

    // Walk interpreter frames using frame->previous (avoids allocations)
    for (; frame != NULL; frame = frame->previous) {
        PyCodeObject* code = NULL;
        int lineno_val = 0;

        extract_frame_info_from_interpreter_frame(frame, &code, &lineno_val);

        if (code != NULL) {
            push_frame_to_sample(sample, code, lineno_val);
        }

        memalloc_debug_gil_release();
    }
}
#endif

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

#if PY_VERSION_HEX >= 0x030b0000 && PY_VERSION_HEX < 0x030d0000
    // For Python 3.11-3.12, use _PyInterpreterFrame directly to avoid allocations
    // Access the interpreter frame from tstate->cframe instead of creating PyFrameObject
    // Note: Python 3.13+ has different internal structures, so we fall back to PyFrameObject
    _PyCFrame* cframe = tstate->cframe;
    if (cframe == NULL)
        return NULL;

    return new traceback_t(ptr, size, domain, weighted_size, tstate, max_nframe);
#elif PY_VERSION_HEX >= 0x030d0000
    // Python 3.13+: internal structures changed, use PyFrameObject API
    PyFrameObject* pyframe = PyThreadState_GetFrame(tstate);
    if (pyframe == NULL)
        return NULL;
    return new traceback_t(ptr, size, domain, weighted_size, pyframe, max_nframe);
#else
    // For older Python versions, use PyFrameObject
#ifdef _PY39_AND_LATER
    PyFrameObject* pyframe = PyThreadState_GetFrame(tstate);
#else
    PyFrameObject* pyframe = tstate->frame;
#endif

    if (pyframe == NULL)
        return NULL;

    return new traceback_t(ptr, size, domain, weighted_size, pyframe, max_nframe);
#endif
}
