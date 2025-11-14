#include <assert.h>
#include <stdlib.h>
#include <string.h>

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

#ifndef MEMALLOC_BUFFER_POOL_CAPACITY
#define MEMALLOC_BUFFER_POOL_CAPACITY 4
#endif

/* Buffer pool for scratch buffers used for collecting tracebacks. We don't know
 * ahead of time how many frames a traceback will have, and traversing the list
 * of frames can be expensive. At the same time, we want to right-size the
 * tracebacks we aggregate in memory to minimize waste. So we use scratch buffers
 * of the maximum configured traceback size and then copy the actual traceback
 * once we know how many frames it has.
 *
 * We need a pool because, for some Python versions, collecting a traceback
 * releases the GIL. So we can't just have one scratch buffer or we will have a
 * logical race in writing to the buffer. The calling thread owns the scratch
 * buffer it gets from the pool until the buffer is returned to the pool.
 *
 * TODO: if/when we support no-GIL or subinterpreter python, we'll need a
 * lock to protect the pool in get & put */
static std::vector<traceback_t*> g_memalloc_tb_buffer_pool;

static traceback_t*
memalloc_tb_buffer_pool_get(uint16_t max_nframe)
{
    assert(PyGILState_Check());
    if (!g_memalloc_tb_buffer_pool.empty()) {
        traceback_t* t = g_memalloc_tb_buffer_pool.back();
        g_memalloc_tb_buffer_pool.pop_back();
        // Reset and reserve space for frames
        t->frames.clear();
        t->frames.reserve(max_nframe);
        t->nframe = 0;
        t->total_nframe = 0;
        return t;
    }
    return new traceback_t(max_nframe);
}

static void
memalloc_tb_buffer_pool_put(traceback_t* t)
{
    assert(PyGILState_Check());
    if (g_memalloc_tb_buffer_pool.size() < MEMALLOC_BUFFER_POOL_CAPACITY) {
        g_memalloc_tb_buffer_pool.push_back(t);
    } else {
        /* We don't want to keep an unbounded number of full-size tracebacks
         * around. So in the rare chance that there are a large number of threads
         * hitting sampling at the same time, just drop excess tracebacks */
        delete t;
    }
}

static void
memalloc_tb_buffer_pool_clear()
{
    assert(PyGILState_Check());
    for (traceback_t* t : g_memalloc_tb_buffer_pool) {
        delete t;
    }
    g_memalloc_tb_buffer_pool.clear();
}

#undef MEMALLOC_BUFFER_POOL_CAPACITY

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

int
memalloc_tb_init(uint16_t max_nframe)
{
    if (unknown_name == NULL) {
        unknown_name = PyUnicode_FromString("<unknown>");
        if (unknown_name == NULL)
            return -1;
        PyUnicode_InternInPlace(&unknown_name);
    }

    if (empty_string == NULL) {
        empty_string = PyUnicode_FromString("");
        if (empty_string == NULL)
            return -1;
        PyUnicode_InternInPlace(&empty_string);
    }
    return 0;
}

void
memalloc_tb_deinit(void)
{
    memalloc_tb_buffer_pool_clear();
}

traceback_t::traceback_t(uint16_t nframe)
  : total_nframe(0)
  , nframe(0)
  , ptr(nullptr)
  , size(0)
  , domain(PYMEM_DOMAIN_OBJ)
  , thread_id(0)
  , reported(false)
  , count(0)
{
    frames.reserve(nframe);
}

traceback_t::~traceback_t()
{
    for (const frame_t& frame : frames) {
        Py_DECREF(frame.filename);
        Py_DECREF(frame.name);
    }
}

void
traceback_free(traceback_t* tb)
{
    delete tb;
}

/* Convert PyFrameObject to a frame_t that we can store in memory */
static void
memalloc_convert_frame(PyFrameObject* pyframe, frame_t* frame)
{
    int lineno = PyFrame_GetLineNumber(pyframe);
    if (lineno < 0)
        lineno = 0;

    frame->lineno = (unsigned int)lineno;

    PyObject *filename, *name;

#ifdef _PY39_AND_LATER
    PyCodeObject* code = PyFrame_GetCode(pyframe);
#else
    PyCodeObject* code = pyframe->f_code;
#endif

    if (code == NULL) {
        filename = unknown_name;
        name = unknown_name;
    } else {
        filename = code->co_filename;
        name = code->co_name;
    }

    if (name)
        frame->name = name;
    else
        frame->name = unknown_name;

    Py_INCREF(frame->name);

    if (filename)
        frame->filename = filename;
    else
        frame->filename = unknown_name;

    Py_INCREF(frame->filename);

#ifdef _PY39_AND_LATER
    Py_XDECREF(code);
#endif
}

static traceback_t*
memalloc_frame_to_traceback(PyFrameObject* pyframe, uint16_t max_nframe)
{
    traceback_t* traceback_buffer = memalloc_tb_buffer_pool_get(max_nframe);
    if (!traceback_buffer) {
        return NULL;
    }
    traceback_buffer->total_nframe = 0;
    traceback_buffer->nframe = 0;
    traceback_buffer->frames.clear();

    for (; pyframe != NULL;) {
        if (traceback_buffer->nframe < max_nframe) {
            frame_t frame;
            memalloc_convert_frame(pyframe, &frame);
            traceback_buffer->frames.push_back(frame);
            traceback_buffer->nframe++;
        }
        /* Make sure we don't overflow */
        if (traceback_buffer->total_nframe < UINT16_MAX)
            traceback_buffer->total_nframe++;

#ifdef _PY39_AND_LATER
        PyFrameObject* back = PyFrame_GetBack(pyframe);
        Py_DECREF(pyframe);
        pyframe = back;
#else
        pyframe = pyframe->f_back;
#endif
        memalloc_debug_gil_release();
    }

    traceback_t* traceback = new traceback_t(traceback_buffer->nframe);
    // Copy the data from the buffer
    traceback->total_nframe = traceback_buffer->total_nframe;
    traceback->nframe = traceback_buffer->nframe;
    traceback->frames = traceback_buffer->frames;
    // Increment ref counts for copied frames
    for (const frame_t& frame : traceback->frames) {
        Py_INCREF(frame.filename);
        Py_INCREF(frame.name);
    }

    memalloc_tb_buffer_pool_put(traceback_buffer);

    return traceback;
}

traceback_t*
memalloc_get_traceback(uint16_t max_nframe, void* ptr, size_t size, PyMemAllocatorDomain domain, size_t weighted_size)
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

    traceback_t* traceback = memalloc_frame_to_traceback(pyframe, max_nframe);

    if (traceback == NULL)
        return NULL;

    traceback->size = weighted_size;
    traceback->ptr = ptr;

    traceback->thread_id = PyThread_get_thread_ident();

    traceback->domain = domain;

    traceback->reported = false;

    // Size 0 allocations are legal and we can hypothetically sample them,
    // e.g. if an allocation during sampling pushes us over the next sampling threshold,
    // but we can't sample it, so we sample the next allocation which happens to be 0
    // bytes. Defensively make sure size isn't 0.
    size = size > 0 ? size : 1;
    double scaled_count = ((double)weighted_size) / ((double)size);
    traceback->count = (size_t)scaled_count;

    return traceback;
}

PyObject*
traceback_to_tuple(traceback_t* tb)
{
    /* Convert stack into a tuple of tuple */
    PyObject* stack = PyTuple_New(tb->nframe);

    for (uint16_t nframe = 0; nframe < tb->nframe; nframe++) {
        PyObject* frame_tuple = PyTuple_New(4);

        const frame_t& frame = tb->frames[nframe];

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
    PyTuple_SET_ITEM(tuple, 1, PyLong_FromUnsignedLong(tb->thread_id));
    return tuple;
}
