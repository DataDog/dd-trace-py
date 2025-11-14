#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <frameobject.h>

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
{
    // Size 0 allocations are legal and we can hypothetically sample them,
    // e.g. if an allocation during sampling pushes us over the next sampling threshold,
    // but we can't sample it, so we sample the next allocation which happens to be 0
    // bytes. Defensively make sure size isn't 0.
    size_t adjusted_size = size > 0 ? size : 1;
    double scaled_count = ((double)weighted_size) / ((double)adjusted_size);
    count = (size_t)scaled_count;

    // Collect frames from the Python frame chain
    size_t total_nframe = 0;
    for (; pyframe != NULL;) {
        if (frames.size() < max_nframe) {
            frames.emplace_back(pyframe);
        }
        // TODO(dsn): add a truncated frame to the traceback if we exceed the max_nframe
        total_nframe++;

#ifdef _PY39_AND_LATER
        PyFrameObject* back = PyFrame_GetBack(pyframe);
        Py_DECREF(pyframe);
        pyframe = back;
#else
        pyframe = pyframe->f_back;
#endif
        memalloc_debug_gil_release();
    }

    // Shrink to actual size to save memory
    frames.shrink_to_fit();
}

traceback_t::~traceback_t()
{
    for (const frame_t& frame : frames) {
        Py_DECREF(frame.filename);
        Py_DECREF(frame.name);
    }
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
