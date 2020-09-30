#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>

#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <frameobject.h>

#if PY_MAJOR_VERSION >= 3 && PY_MINOR_VERSION >= 9
#define _PY39_AND_LATER
#endif

#if PY_MAJOR_VERSION >= 3 && PY_MINOR_VERSION >= 7
#define _PY37_AND_LATER
#endif

typedef struct
{
    PyMemAllocatorEx pymem_allocator_obj;
    uint32_t max_events;
    uint16_t max_nframe;
} memalloc_context_t;

/* We only support being started once, so we use a global context for the whole
   module. If we ever want to be started multiple twice, we'd need a more
   object-oriented approach and allocate a context per object.
*/
static memalloc_context_t global_memalloc_ctx;

typedef struct
#ifdef __GNUC__
  __attribute__((packed))
#elif defined(_MSC_VER)
#pragma pack(push, 4)
#endif
{
    PyObject* filename;
    PyObject* name;
    unsigned int lineno;
} frame_t;

typedef struct
{
    /* Total number of frames in the traceback */
    uint16_t total_nframe;
    /* Number of frames in the traceback */
    uint16_t nframe;
    /* Memory size allocated in bytes */
    size_t size;
    /* Thread ID */
    unsigned long thread_id;
    /* List of frames, top frame first */
    frame_t frames[1];
} traceback_t;

/* The maximum number of frames is either:
   - The maximum number of frames we can store in `traceback_t.nframe`
   - The maximum memory size_t we can allocate */
static const unsigned long MAX_NFRAME = Py_MIN(UINT16_MAX, (SIZE_MAX - sizeof(traceback_t)) / sizeof(frame_t) + 1);

/* The maximum number of tracebacks is either:
   - The maximum number of traceback we can store in `traceback_list_t.size`
   - The maximum memory size_t we can allocate */
static const unsigned long MAX_EVENTS = Py_MIN(UINT32_MAX, (SIZE_MAX - sizeof(uint32_t)) / sizeof(traceback_t));

/* Temporary traceback buffer to store new traceback */
static traceback_t* traceback_buffer = NULL;

/* List of current traceback */
typedef struct
{
    traceback_t** tracebacks;
    uint32_t count;
    uint64_t alloc_count;
} traceback_list_t;

static traceback_list_t* global_traceback_list;

/* A string containing "<unknown>" just in case we can't store the real function
 * or file name. */
static PyObject* unknown_name = NULL;
/* The number 1 */
static PyObject* number_one = NULL;

static void
traceback_free(traceback_t* tb)
{
    for (uint16_t nframe = 0; nframe < tb->nframe; nframe++) {
        Py_DECREF(tb->frames[nframe].filename);
        Py_DECREF(tb->frames[nframe].name);
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

    if (code == NULL) {
        filename = unknown_name;
        name = unknown_name;
    } else {
        filename = code->co_filename;
        name = code->co_name;
    }

#ifdef _PY39_AND_LATER
    Py_DECREF(code);
#endif

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
}

#define TRACEBACK_SIZE(NFRAME) (sizeof(traceback_t) + sizeof(frame_t) * (NFRAME - 1))

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

static uint64_t
random_range(uint64_t max)
{
    /* Return a random number between [0, max[ */
    return (uint64_t)((double)rand() / ((double)RAND_MAX + 1) * max);
}

static traceback_t*
memalloc_get_traceback(uint16_t max_nframe, size_t size)
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

#ifdef _PY37_AND_LATER
    traceback->thread_id = PyThread_get_thread_ident();
#else
    traceback->thread_id = tstate->thread_id;
#endif

    return traceback;
}

static void
memalloc_add_event(size_t size, memalloc_context_t* ctx)
{
    /* Do not overflow; just ignore the new events if we ever reach that point */
    if (global_traceback_list->alloc_count >= UINT64_MAX)
        return;

    global_traceback_list->alloc_count++;

    /* Determine if we can capture or if we need to sample */
    if (global_traceback_list->count < ctx->max_events) {
        /* Buffer is not full, fill it */
        traceback_t* tb = memalloc_get_traceback(ctx->max_nframe, size);
        if (tb) {
            global_traceback_list->tracebacks[global_traceback_list->count] = tb;
            global_traceback_list->count++;
        }
    } else {
        /* Sampling mode using a reservoir sampling algorithm */
        uint64_t r = random_range(global_traceback_list->alloc_count);

        if (r < ctx->max_events) {
            /* Replace a random traceback with this one */
            traceback_t* tb = memalloc_get_traceback(ctx->max_nframe, size);
            if (tb) {
                traceback_free(global_traceback_list->tracebacks[r]);
                global_traceback_list->tracebacks[r] = tb;
            }
        }
    }
}

static void
memalloc_free(void* ctx, void* ptr)
{
    PyMemAllocatorEx* alloc = (PyMemAllocatorEx*)ctx;

    if (ptr == NULL)
        return;

    alloc->free(alloc->ctx, ptr);
}

static void*
memalloc_alloc(int use_calloc, void* ctx, size_t nelem, size_t elsize)
{
    void* ptr;
    memalloc_context_t* memalloc_ctx = (memalloc_context_t*)ctx;

    if (use_calloc)
        ptr = memalloc_ctx->pymem_allocator_obj.calloc(memalloc_ctx->pymem_allocator_obj.ctx, nelem, elsize);
    else
        ptr = memalloc_ctx->pymem_allocator_obj.malloc(memalloc_ctx->pymem_allocator_obj.ctx, nelem * elsize);

    if (ptr)
        memalloc_add_event(nelem * elsize, memalloc_ctx);

    return ptr;
}

static void*
memalloc_malloc(void* ctx, size_t size)
{
    return memalloc_alloc(0, ctx, 1, size);
}

static void*
memalloc_calloc(void* ctx, size_t nelem, size_t elsize)
{
    return memalloc_alloc(1, ctx, nelem, elsize);
}

static void*
memalloc_realloc(void* ctx, void* ptr, size_t new_size)
{
    memalloc_context_t* memalloc_ctx = (memalloc_context_t*)ctx;
    void* ptr2 = memalloc_ctx->pymem_allocator_obj.realloc(memalloc_ctx->pymem_allocator_obj.ctx, ptr, new_size);

    if (ptr2)
        memalloc_add_event(new_size, memalloc_ctx);

    return ptr2;
}

static traceback_list_t*
traceback_list_new()
{
    traceback_list_t* traceback_list = PyMem_RawMalloc(sizeof(traceback_list_t));
    traceback_list->tracebacks = PyMem_RawMalloc(sizeof(traceback_t*) * global_memalloc_ctx.max_events);
    traceback_list->count = 0;
    traceback_list->alloc_count = 0;
    return traceback_list;
}

static void
traceback_list_free(traceback_list_t* tb_list)
{
    PyMem_RawFree(tb_list->tracebacks);
    PyMem_RawFree(tb_list);
}

static int
_memalloc_init_constants(void)
{
    if (unknown_name == NULL) {
        unknown_name = PyUnicode_FromString("<unknown>");
        if (unknown_name == NULL)
            return -1;
        PyUnicode_InternInPlace(&unknown_name);
    }

    if (number_one == NULL) {
        number_one = PyLong_FromLong(1);
        if (number_one == NULL)
            return -1;
    }

    return 0;
}

PyDoc_STRVAR(memalloc_start__doc__, "start($module, max_nframe, max_events)\n"
                                    "--\n"
                                    "\n"
                                    "Start tracing Python memory allocations.\n"
                                    "\n"
                                    "Sets the maximum number of frames stored in the traceback of a\n"
                                    "trace to max_nframe and the maximum number of events to max_events.");
static PyObject*
memalloc_start(PyObject* Py_UNUSED(module), PyObject* args)
{
    if (global_traceback_list) {
        PyErr_SetString(PyExc_RuntimeError, "the memalloc module is already started");
        return NULL;
    }

    int max_nframe, max_events;

    if (!PyArg_ParseTuple(args, "ii", &max_nframe, &max_events))
        return NULL;

    if (max_nframe < 1 || ((unsigned long)max_nframe) > MAX_NFRAME) {
        PyErr_Format(PyExc_ValueError, "the number of frames must be in range [1; %lu]", MAX_NFRAME);
        return NULL;
    }

    global_memalloc_ctx.max_nframe = (uint16_t)max_nframe;

    if (max_events < 1 || ((unsigned long)max_events) > MAX_EVENTS) {
        PyErr_Format(PyExc_ValueError, "the number of events must be in range [1; %lu]", MAX_EVENTS);
        return NULL;
    }

    global_memalloc_ctx.max_events = (uint32_t)max_events;

    PyMemAllocatorEx alloc;

    alloc.malloc = memalloc_malloc;
    alloc.calloc = memalloc_calloc;
    alloc.realloc = memalloc_realloc;
    alloc.free = memalloc_free;

    alloc.ctx = &global_memalloc_ctx;
    PyMem_GetAllocator(PYMEM_DOMAIN_OBJ, &global_memalloc_ctx.pymem_allocator_obj);
    PyMem_SetAllocator(PYMEM_DOMAIN_OBJ, &alloc);

    global_traceback_list = traceback_list_new();

    /* Allocate a buffer that can handle the largest traceback possible.
       This will be used a temporary buffer when converting stack traces. */
    traceback_buffer = PyMem_RawMalloc(TRACEBACK_SIZE(global_memalloc_ctx.max_nframe));

    if (_memalloc_init_constants()) {
        PyMem_RawFree(traceback_buffer);
        traceback_list_free(global_traceback_list);
        return NULL;
    }

    Py_RETURN_NONE;
}

static PyObject*
traceback_to_tuple(traceback_t* tb, bool incref)
{
    /* Convert stack into a tuple of tuple */
    PyObject* stack = PyTuple_New(tb->nframe);

    for (uint16_t nframe = 0; nframe < tb->nframe; nframe++) {
        PyObject* frame_tuple = PyTuple_New(3);

        frame_t* frame = &tb->frames[nframe];

        PyTuple_SET_ITEM(frame_tuple, 0, frame->filename);
        PyTuple_SET_ITEM(frame_tuple, 1, PyLong_FromUnsignedLong(frame->lineno));
        PyTuple_SET_ITEM(frame_tuple, 2, frame->name);

        /* No need to Py_INCREF({filename,name}) if the tuple hijack the
           reference and we then free the traceback_t */
        if (incref) {
            Py_INCREF(frame->filename);
            Py_INCREF(frame->name);
        }

        PyTuple_SET_ITEM(stack, nframe, frame_tuple);
    }

    PyObject* tuple = PyTuple_New(3);

    PyTuple_SET_ITEM(tuple, 0, stack);
    PyTuple_SET_ITEM(tuple, 1, PyLong_FromUnsignedLong(tb->total_nframe));
    PyTuple_SET_ITEM(tuple, 2, PyLong_FromUnsignedLong(tb->thread_id));

    return tuple;
}

static void
traceback_list_free_tracebacks(traceback_list_t* tb_list)
{
    for (uint32_t i = 0; i < tb_list->count; i++)
        traceback_free(tb_list->tracebacks[i]);
}

PyDoc_STRVAR(memalloc_stop__doc__, "stop($module, /)\n"
                                   "--\n"
                                   "\n"
                                   "Stop tracing Python memory allocations.\n"
                                   "\n"
                                   "Also clear traces of memory blocks allocated by Python.");
static PyObject*
memalloc_stop(PyObject* Py_UNUSED(module), PyObject* Py_UNUSED(args))
{
    if (!global_traceback_list) {
        PyErr_SetString(PyExc_RuntimeError, "the memalloc module was not started");
        return NULL;
    }

    PyMem_SetAllocator(PYMEM_DOMAIN_OBJ, &global_memalloc_ctx.pymem_allocator_obj);
    PyMem_RawFree(traceback_buffer);
    traceback_list_free_tracebacks(global_traceback_list);
    traceback_list_free(global_traceback_list);
    global_traceback_list = NULL;

    Py_RETURN_NONE;
}

typedef struct
{
    PyObject_HEAD traceback_list_t* traceback_list;
    Py_ssize_t seq_index;
} IterEventsState;

PyDoc_STRVAR(iterevents__doc__, "iter_events()\n"
                                "--\n"
                                "\n"
                                "Returns a tuple with 3 items:\n:"
                                "1. an iterator of memory allocation traced so far\n"
                                "2. the number of items in the iterator\n"
                                "3. the total number of allocations since last reset\n"
                                "\n"
                                "Also reset the traces of memory blocks allocated by Python.");
static PyObject*
iterevents_new(PyTypeObject* type, PyObject* Py_UNUSED(args), PyObject* Py_UNUSED(kwargs))
{
    if (!global_traceback_list) {
        PyErr_SetString(PyExc_RuntimeError, "the memalloc module was not started");
        return NULL;
    }

    IterEventsState* iestate = (IterEventsState*)type->tp_alloc(type, 0);
    if (!iestate)
        return NULL;

    iestate->traceback_list = global_traceback_list;
    /* reset the current traceback list */
    global_traceback_list = traceback_list_new();
    iestate->seq_index = 0;

    PyObject* iter_and_count = PyTuple_New(3);
    PyTuple_SET_ITEM(iter_and_count, 0, (PyObject*)iestate);
    PyTuple_SET_ITEM(iter_and_count, 1, PyLong_FromUnsignedLong(iestate->traceback_list->count));
    PyTuple_SET_ITEM(iter_and_count, 2, PyLong_FromUnsignedLongLong(iestate->traceback_list->alloc_count));

    return iter_and_count;
}

static void
iterevents_dealloc(IterEventsState* iestate)
{
    traceback_list_free_tracebacks(iestate->traceback_list);
    traceback_list_free(iestate->traceback_list);
    Py_TYPE(iestate)->tp_free(iestate);
}

static PyObject*
iterevents_next(IterEventsState* iestate)
{
    if (0 <= iestate->seq_index && iestate->seq_index < iestate->traceback_list->count) {
        traceback_t* tb = iestate->traceback_list->tracebacks[iestate->seq_index];
        iestate->seq_index++;

        PyObject* tb_and_size = PyTuple_New(2);
        PyTuple_SET_ITEM(tb_and_size, 0, traceback_to_tuple(tb, true));
        PyTuple_SET_ITEM(tb_and_size, 1, PyLong_FromSize_t(tb->size));

        return tb_and_size;
    }

    /* Returning NULL in this case is enough. The next() builtin will raise the
     * StopIteration error for us.
     */
    return NULL;
}

static PyTypeObject MemallocIterEvents_Type = {
    PyVarObject_HEAD_INIT(NULL, 0) "iter_events", /* tp_name */
    sizeof(IterEventsState),                      /* tp_basicsize */
    0,                                            /* tp_itemsize */
    (destructor)iterevents_dealloc,               /* tp_dealloc */
    0,                                            /* tp_print */
    0,                                            /* tp_getattr */
    0,                                            /* tp_setattr */
    0,                                            /* tp_reserved */
    0,                                            /* tp_repr */
    0,                                            /* tp_as_number */
    0,                                            /* tp_as_sequence */
    0,                                            /* tp_as_mapping */
    0,                                            /* tp_hash */
    0,                                            /* tp_call */
    0,                                            /* tp_str */
    0,                                            /* tp_getattro */
    0,                                            /* tp_setattro */
    0,                                            /* tp_as_buffer */
    Py_TPFLAGS_DEFAULT,                           /* tp_flags */
    iterevents__doc__,                            /* tp_doc */
    0,                                            /* tp_traverse */
    0,                                            /* tp_clear */
    0,                                            /* tp_richcompare */
    0,                                            /* tp_weaklistoffset */
    PyObject_SelfIter,                            /* tp_iter */
    (iternextfunc)iterevents_next,                /* tp_iternext */
    0,                                            /* tp_methods */
    0,                                            /* tp_members */
    0,                                            /* tp_getset */
    0,                                            /* tp_base */
    0,                                            /* tp_dict */
    0,                                            /* tp_descr_get */
    0,                                            /* tp_descr_set */
    0,                                            /* tp_dictoffset */
    0,                                            /* tp_init */
    PyType_GenericAlloc,                          /* tp_alloc */
    iterevents_new,                               /* tp_new */
};

static PyMethodDef module_methods[] = { { "start", (PyCFunction)memalloc_start, METH_VARARGS, memalloc_start__doc__ },
                                        { "stop", (PyCFunction)memalloc_stop, METH_NOARGS, memalloc_stop__doc__ },
                                        /* sentinel */
                                        { NULL, NULL } };

PyDoc_STRVAR(module_doc, "Module to trace memory blocks allocated by Python.");

static struct PyModuleDef module_def = {
    PyModuleDef_HEAD_INIT, "_memalloc", module_doc, 0, /* non-negative size to be able to unload the module */
    module_methods,        NULL,
};

PyMODINIT_FUNC
PyInit__memalloc(void)
{
    PyObject* m;
    m = PyModule_Create(&module_def);
    if (m == NULL)
        return NULL;

    if (PyType_Ready(&MemallocIterEvents_Type) < 0)
        return NULL;
    Py_INCREF((PyObject*)&MemallocIterEvents_Type);
#ifdef _PY39_AND_LATER
    PyModule_AddType(m, &MemallocIterEvents_Type);
#else
    PyModule_AddObject(m, "iter_events", (PyObject*)&MemallocIterEvents_Type);
#endif

    return m;
}
