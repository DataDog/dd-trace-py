#include <string.h>

#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <frameobject.h>

#include "_memalloc_tb.h"
#include "_pymacro.h"

/* Temporary traceback buffer to store new traceback */
static traceback_t* traceback_buffer = NULL;

/* A string containing "<unknown>" just in case we can't store the real function
 * or file name. */
static PyObject* unknown_name = NULL;
/* A string containing "" */
static PyObject* empty_string = NULL;

#define TRACEBACK_SIZE(NFRAME) (sizeof(traceback_t) + sizeof(frame_t) * (NFRAME - 1))

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

    /* Allocate a buffer that can handle the largest traceback possible.
       This will be used a temporary buffer when converting stack traces. */
    traceback_buffer = PyMem_RawMalloc(TRACEBACK_SIZE(max_nframe));

    if (traceback_buffer == NULL)
        return -1;

    return 0;
}

void
memalloc_tb_deinit(void)
{
    PyMem_RawFree(traceback_buffer);
}

void
traceback_free(traceback_t* tb)
{
    for (uint16_t nframe = 0; nframe < tb->nframe; nframe++) {
        Py_DECREF(tb->frames[nframe].filename);
        Py_DECREF(tb->frames[nframe].name);
        Py_XDECREF(tb->frames[nframe].type);
    }
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

    frame->type = NULL;

    if (code == NULL) {
        filename = unknown_name;
        name = unknown_name;
    } else {
        /* This whole block code is optimized to not allocate any piece of
           Python memory: if it ends up allocating memory, the program will
           crash in a recursive loop. Even if we'd fix that loop with a
           re-entrant barrier, for performance reason we want to be sure we
           don't allocate anything.*/
        filename = code->co_filename;
        name = code->co_name;

        PyObject* co_varnames = code->co_varnames;
        PyObject** f_localsplus = pyframe->f_localsplus;

        if (f_localsplus && co_varnames && PyTuple_GET_SIZE(co_varnames) > 0) {
            PyObject* argname = PyTuple_GetItem(co_varnames, 0);

            if (argname && PyUnicode_Check(argname)) {
                PyObject* value = f_localsplus[0];

                if (value) {
                    Py_ssize_t wlen;
                    /* Warning: tracking PyMem allocation in the future will
                     * break this as it will loop */
                    wchar_t* wstr = PyUnicode_AsWideCharString(argname, &wlen);

                    if (wstr) {
                        if (wcscmp(wstr, L"self") == 0) {
                            frame->type = PyObject_Type(value);
                        } else if (wcscmp(wstr, L"cls") == 0) {
                            if (PyType_Check(value)) {
                                frame->type = value;
                                Py_INCREF(value);
                            }
                        }

                        PyMem_Free(wstr);
                    }
                }
            }
        }
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
    }

    size_t traceback_size = TRACEBACK_SIZE(traceback_buffer->nframe);
    traceback_t* traceback = PyMem_RawMalloc(traceback_size);

    if (traceback)
        memcpy(traceback, traceback_buffer, traceback_size);

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

#ifdef _PY37_AND_LATER
    traceback->thread_id = PyThread_get_thread_ident();
#else
    traceback->thread_id = tstate->thread_id;
#endif

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

        PyObject* class_name;

        if (frame->type) {
            PyTypeObject* type = (PyTypeObject*)frame->type;
            /* Improvement for Python 3.11+? */
            // class_name = _PyType_GetQualName(type);
            class_name = PyUnicode_FromString(type->tp_name);
        } else {
            class_name = empty_string;
            Py_INCREF(class_name);
        }
        PyTuple_SET_ITEM(frame_tuple, 3, class_name);

        PyTuple_SET_ITEM(stack, nframe, frame_tuple);
    }

    PyObject* tuple = PyTuple_New(3);

    PyTuple_SET_ITEM(tuple, 0, stack);
    PyTuple_SET_ITEM(tuple, 1, PyLong_FromUnsignedLong(tb->total_nframe));
    PyTuple_SET_ITEM(tuple, 2, PyLong_FromUnsignedLong(tb->thread_id));

    return tuple;
}
