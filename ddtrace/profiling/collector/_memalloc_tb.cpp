#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <frameobject.h>
#include <string_view>

#include "_memalloc_debug.h"
#include "_memalloc_tb.h"
#include "_pymacro.h"

/* A string containing "<unknown>" just in case we can't store the real function
 * or file name. */
static PyObject* unknown_name = NULL;
/* A string containing "" */
static PyObject* empty_string = NULL;

static PyObject* ddframe_class = NULL;

bool
memalloc_ddframe_class_init()
{
    // If this is double-initialized for some reason, then clean up what we had
    if (ddframe_class) {
        Py_DECREF(ddframe_class);
        ddframe_class = NULL;
    }

    // Import the module that contains the DDFrame class
    PyObject* mod_path = PyUnicode_DecodeFSDefault("ddtrace.profiling.event");
    PyObject* mod = PyImport_Import(mod_path);
    Py_XDECREF(mod_path);
    if (mod == NULL) {
        PyErr_Print();
        return false;
    }

    // Get the DDFrame class object
    ddframe_class = PyObject_GetAttrString(mod, "DDFrame");
    Py_XDECREF(mod);

    // Basic sanity check that the object is the type of object we actually want
    if (ddframe_class == NULL || !PyCallable_Check(ddframe_class)) {
        PyErr_Print();
        return false;
    }

    return true;
}

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
    return true;
}

void
traceback_t::deinit()
{
    // Nothing to clean up - tracebacks are managed by their owners
}

frame_t::frame_t(PyFrameObject* pyframe)
{
    int lineno_val = PyFrame_GetLineNumber(pyframe);
    if (lineno_val < 0)
        lineno_val = 0;

    lineno = (unsigned int)lineno_val;

#ifdef _PY39_AND_LATER
    PyCodeObject* code = PyFrame_GetCode(pyframe);
#else
    PyCodeObject* code = pyframe->f_code;
#endif

    if (code == NULL) {
        name = unknown_name;
        filename = unknown_name;
    } else {
        name = code->co_name ? code->co_name : unknown_name;
        filename = code->co_filename ? code->co_filename : unknown_name;
    }

    Py_INCREF(name);
    Py_INCREF(filename);

#ifdef _PY39_AND_LATER
    Py_XDECREF(code);
#endif
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
  , sample(Datadog::SampleType::Allocation, max_nframe)
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

    // TODO: Ensure profile_state is initialized before creating Sample objects
    // Currently, push_alloc() has bounds checking to handle uninitialized profile_state gracefully,
    // but ideally we should initialize profile_state before constructing traceback_t objects.

    // Push allocation info to sample
    // Note: push_alloc() has bounds checking to handle uninitialized profile_state gracefully
    sample.push_alloc(weighted_size, count);
    sample.push_threadinfo(thread_id, 0, ""); // thread_native_id and thread_name not available here

    // Collect frames from the Python frame chain and push to Sample
    // We push frames as we collect them (root to leaf order).
    // Note: Sample.push_frame() comment says it "Assumes frames are pushed in leaf-order",
    // but we push root-to-leaf. When flushing the sample, use flush_sample(reverse_locations=true)
    // to reverse the order if needed.
    size_t total_nframe = 0;
    for (PyFrameObject* frame = pyframe; frame != NULL;) {
        if (frames.size() < max_nframe) {
            frames.emplace_back(frame);
        }
        // TODO(dsn): add a truncated frame to the traceback if we exceed the max_nframe
        total_nframe++;

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

    // Shrink to actual size to save memory
    frames.shrink_to_fit();
}

traceback_t::~traceback_t()
{
    // Clean up Python references in frames
    for (const frame_t& frame : frames) {
        Py_DECREF(frame.filename);
        Py_DECREF(frame.name);
    }
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

PyObject*
traceback_t::to_tuple() const
{
    /* Convert stack into a tuple of tuple */
    PyObject* stack = PyTuple_New(frames.size());

    for (size_t nframe = 0; nframe < frames.size(); nframe++) {
        PyObject* frame_tuple = PyTuple_New(4);

        const frame_t& frame = frames[nframe];

        Py_INCREF(frame.filename);
        PyTuple_SET_ITEM(frame_tuple, 0, frame.filename);
        PyTuple_SET_ITEM(frame_tuple, 1, PyLong_FromUnsignedLong(frame.lineno));
        Py_INCREF(frame.name);
        PyTuple_SET_ITEM(frame_tuple, 2, frame.name);
        /* Class name */
        Py_INCREF(empty_string);
        PyTuple_SET_ITEM(frame_tuple, 3, empty_string);

        // Try to set the class.  If we cannot (e.g., if the sofile is reloaded
        // without module initialization), then this will result in an error if
        // the underlying object is used via attributes
        if (ddframe_class) {
#if PY_VERSION_HEX >= 0x03090000
            Py_SET_TYPE(frame_tuple, (PyTypeObject*)ddframe_class);
#else
            Py_TYPE(frame_tuple) = (PyTypeObject*)ddframe_class;
#endif
            Py_INCREF(ddframe_class);
        }

        PyTuple_SET_ITEM(stack, nframe, frame_tuple);
    }

    PyObject* tuple = PyTuple_New(2);
    PyTuple_SET_ITEM(tuple, 0, stack);
    PyTuple_SET_ITEM(tuple, 1, PyLong_FromUnsignedLong(thread_id));
    return tuple;
}
