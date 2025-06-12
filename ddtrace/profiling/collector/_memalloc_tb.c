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

#define TRACEBACK_SIZE(NFRAME) (sizeof(traceback_t) + sizeof(frame_t) * (NFRAME - 1))

static PyObject* ddframe_class = NULL;

#ifndef MEMALLOC_BUFFER_POOL_CAPACITY
#define MEMALLOC_BUFFER_POOL_CAPACITY 4
#endif

/* memalloc_tb_buffer_pool is a pool of scratch buffers used for collecting
 * tracebacks. We don't know ahead of time how many frames a traceback will have,
 * and traversing the list of frames can be expensive. At the same time, we want
 * to right-size the tracebacks we aggregate in memory to minimize waste. So we
 * use scratch buffers of the maximum configured traceback size and then copy
 * the actual traceback once we know how many frames it has.
 *
 * We need a pool because, for some Python versions, collecting a traceback
 * releases the GIL. So we can't just have one scratch buffer or we will have a
 * logical race in writing to the buffer. The calling thread owns the scratch
 * buffer it gets from the pool until the buffer is returned to the pool.
 */
typedef struct
{
    /* TODO: if/when we support no-GIL or subinterpreter python, we'll need a
     * lock to protect the pool in get & put */
    traceback_t* pool[MEMALLOC_BUFFER_POOL_CAPACITY];
    size_t count;
    size_t capacity;
} memalloc_tb_buffer_pool;

/* For now we use a global pool */
static memalloc_tb_buffer_pool g_memalloc_tb_buffer_pool = {
    .count = 0,
    .capacity = MEMALLOC_BUFFER_POOL_CAPACITY,
};

#undef MEMALLOC_BUFFER_POOL_CAPACITY

static traceback_t*
memalloc_tb_buffer_pool_get(memalloc_tb_buffer_pool* pool, uint16_t max_nframe)
{
    assert(PyGILState_Check());
    traceback_t* t = NULL;
    if (pool->count > 0) {
        t = pool->pool[pool->count - 1];
        pool->pool[pool->count - 1] = NULL;
        pool->count--;
    } else {
        t = malloc(TRACEBACK_SIZE(max_nframe));
    }
    return t;
}

static void
memalloc_tb_buffer_pool_put(memalloc_tb_buffer_pool* pool, traceback_t* t)
{
    assert(PyGILState_Check());
    if (pool->count < pool->capacity) {
        pool->pool[pool->count] = t;
        pool->count++;
    } else {
        /* We don't want to keep an unbounded number of full-size tracebacks
         * around. So in the rare chance that there are a large number of threads
         * hitting sampling at the same time, just drop excess tracebacks */
        free(t);
    }
}

static void
memalloc_tb_buffer_pool_clear(memalloc_tb_buffer_pool* pool)
{
    assert(PyGILState_Check());
    for (size_t i = 0; i < pool->count; i++) {
        free(pool->pool[i]);
        pool->pool[i] = NULL;
    }
    pool->count = 0;
}

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
    memalloc_tb_buffer_pool_clear(&g_memalloc_tb_buffer_pool);
}

void
traceback_free(traceback_t* tb)
{
    if (!tb)
        return;

    for (uint16_t nframe = 0; nframe < tb->nframe; nframe++) {
        Py_DECREF(tb->frames[nframe].filename);
        Py_DECREF(tb->frames[nframe].name);
    }
    memalloc_debug_gil_release();
    PyMem_RawFree(tb);
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
    traceback_t* traceback_buffer = memalloc_tb_buffer_pool_get(&g_memalloc_tb_buffer_pool, max_nframe);
    if (!traceback_buffer) {
        return NULL;
    }
    traceback_buffer->total_nframe = 0;
    traceback_buffer->nframe = 0;

    for (; pyframe != NULL;) {
        if (traceback_buffer->nframe < max_nframe) {
            memalloc_convert_frame(pyframe, &traceback_buffer->frames[traceback_buffer->nframe]);
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

    size_t traceback_size = TRACEBACK_SIZE(traceback_buffer->nframe);
    traceback_t* traceback = PyMem_RawMalloc(traceback_size);

    if (traceback)
        memcpy(traceback, traceback_buffer, traceback_size);

    memalloc_tb_buffer_pool_put(&g_memalloc_tb_buffer_pool, traceback_buffer);

    return traceback;
}

traceback_t*
memalloc_get_traceback(uint16_t max_nframe, void* ptr, size_t size, PyMemAllocatorDomain domain)
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

    traceback->size = size;
    traceback->ptr = ptr;

    traceback->thread_id = PyThread_get_thread_ident();

    traceback->domain = domain;

    return traceback;
}

PyObject*
traceback_to_tuple(traceback_t* tb)
{
    /* Convert stack into a tuple of tuple */
    PyObject* stack = PyTuple_New(tb->nframe);

    for (uint16_t nframe = 0; nframe < tb->nframe; nframe++) {
        PyObject* frame_tuple = PyTuple_New(4);

        frame_t* frame = &tb->frames[nframe];

        PyTuple_SET_ITEM(frame_tuple, 0, frame->filename);
        Py_INCREF(frame->filename);
        PyTuple_SET_ITEM(frame_tuple, 1, PyLong_FromUnsignedLong(frame->lineno));
        PyTuple_SET_ITEM(frame_tuple, 2, frame->name);
        Py_INCREF(frame->name);
        /* Class name */
        PyTuple_SET_ITEM(frame_tuple, 3, empty_string);
        Py_INCREF(empty_string);

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

    PyObject* tuple = PyTuple_New(3);
    PyTuple_SET_ITEM(tuple, 0, stack);
    PyTuple_SET_ITEM(tuple, 1, PyLong_FromUnsignedLong(tb->total_nframe));
    PyTuple_SET_ITEM(tuple, 2, PyLong_FromUnsignedLong(tb->thread_id));
    return tuple;
}
