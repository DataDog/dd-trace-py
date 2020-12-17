#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>

#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <frameobject.h>

#include "_memalloc_tb.h"
#include "_pymacro.h"

typedef struct
{
    PyMemAllocatorEx pymem_allocator_obj;
    uint16_t max_events;
    uint16_t max_nframe;
} memalloc_context_t;

/* We only support being started once, so we use a global context for the whole
   module. If we ever want to be started multiple twice, we'd need a more
   object-oriented approach and allocate a context per object.
*/
static memalloc_context_t global_memalloc_ctx;

static traceback_list_t* global_traceback_list;

static uint64_t
random_range(uint64_t max)
{
    /* Return a random number between [0, max[ */
    return (uint64_t)((double)rand() / ((double)RAND_MAX + 1) * max);
}

static void
memalloc_add_event(memalloc_context_t* ctx, void* ptr, size_t size)
{
    /* Do not overflow; just ignore the new events if we ever reach that point */
    if (global_traceback_list->alloc_count >= UINT64_MAX)
        return;

    global_traceback_list->alloc_count++;

    /* Determine if we can capture or if we need to sample */
    if (global_traceback_list->count < ctx->max_events) {
        /* Buffer is not full, fill it */
        traceback_t* tb = memalloc_get_traceback(ctx->max_nframe, ptr, size);
        if (tb) {
            global_traceback_list->tracebacks[global_traceback_list->count] = tb;
            global_traceback_list->count++;
        }
    } else {
        /* Sampling mode using a reservoir sampling algorithm */
        uint64_t r = random_range(global_traceback_list->alloc_count);

        if (r < ctx->max_events) {
            /* Replace a random traceback with this one */
            traceback_t* tb = memalloc_get_traceback(ctx->max_nframe, ptr, size);
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
        memalloc_add_event(memalloc_ctx, ptr, nelem * elsize);

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
        memalloc_add_event(memalloc_ctx, ptr2, new_size);

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

PyDoc_STRVAR(memalloc_start__doc__,
             "start($module, max_nframe, max_events)\n"
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

    long max_nframe, max_events;

    /* Store short int in long so we're sure they fit */
    if (!PyArg_ParseTuple(args, "ll", &max_nframe, &max_events))
        return NULL;

    if (max_nframe < 1 || max_nframe > TRACEBACK_MAX_NFRAME) {
        PyErr_Format(PyExc_ValueError, "the number of frames must be in range [1; %lu]", TRACEBACK_MAX_NFRAME);
        return NULL;
    }

    global_memalloc_ctx.max_nframe = (uint16_t)max_nframe;

    if (max_events < 1 || max_events > TRACEBACK_LIST_MAX_COUNT) {
        PyErr_Format(PyExc_ValueError, "the number of events must be in range [1; %lu]", TRACEBACK_LIST_MAX_COUNT);
        return NULL;
    }

    global_memalloc_ctx.max_events = (uint16_t)max_events;

    if (memalloc_tb_init(global_memalloc_ctx.max_nframe) < 0)
        return NULL;

    PyMemAllocatorEx alloc;

    alloc.malloc = memalloc_malloc;
    alloc.calloc = memalloc_calloc;
    alloc.realloc = memalloc_realloc;
    alloc.free = memalloc_free;

    alloc.ctx = &global_memalloc_ctx;

    global_traceback_list = traceback_list_new();

    PyMem_GetAllocator(PYMEM_DOMAIN_OBJ, &global_memalloc_ctx.pymem_allocator_obj);
    PyMem_SetAllocator(PYMEM_DOMAIN_OBJ, &alloc);

    Py_RETURN_NONE;
}

static PyObject*
traceback_to_tuple(traceback_t* tb)
{
    /* Convert stack into a tuple of tuple */
    PyObject* stack = PyTuple_New(tb->nframe);

    for (uint16_t nframe = 0; nframe < tb->nframe; nframe++) {
        PyObject* frame_tuple = PyTuple_New(3);

        frame_t* frame = &tb->frames[nframe];

        PyTuple_SET_ITEM(frame_tuple, 0, frame->filename);
        Py_INCREF(frame->filename);
        PyTuple_SET_ITEM(frame_tuple, 1, PyLong_FromUnsignedLong(frame->lineno));
        PyTuple_SET_ITEM(frame_tuple, 2, frame->name);
        Py_INCREF(frame->name);

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

PyDoc_STRVAR(memalloc_stop__doc__,
             "stop($module, /)\n"
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
    memalloc_tb_deinit();
    traceback_list_free_tracebacks(global_traceback_list);
    traceback_list_free(global_traceback_list);
    global_traceback_list = NULL;

    Py_RETURN_NONE;
}

typedef struct
{
    PyObject_HEAD traceback_list_t* traceback_list;
    uint32_t seq_index;
} IterEventsState;

PyDoc_STRVAR(iterevents__doc__,
             "iter_events()\n"
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
    if (iestate->seq_index < iestate->traceback_list->count) {
        traceback_t* tb = iestate->traceback_list->tracebacks[iestate->seq_index];
        iestate->seq_index++;

        PyObject* tb_and_size = PyTuple_New(2);
        PyTuple_SET_ITEM(tb_and_size, 0, traceback_to_tuple(tb));
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
    0,                                            /* tp_free */
    0,                                            /* tp_is_gc */
    0,                                            /* tp_bases */
    0,                                            /* tp_mro */
    0,                                            /* tp_cache */
    0,                                            /* tp_subclass */
    0,                                            /* tp_subclasses */
    0,                                            /* tp_del */
    0,                                            /* tp_version_tag */
    0,                                            /* tp_finalize */
#ifdef _PY38_AND_LATER
    0, /* tp_vectorcall */
#endif
#ifdef _PY38
    0, /* tp_print */
#endif
};

static PyMethodDef module_methods[] = { { "start", (PyCFunction)memalloc_start, METH_VARARGS, memalloc_start__doc__ },
                                        { "stop", (PyCFunction)memalloc_stop, METH_NOARGS, memalloc_stop__doc__ },
                                        /* sentinel */
                                        { NULL, NULL, 0, NULL } };

PyDoc_STRVAR(module_doc, "Module to trace memory blocks allocated by Python.");

static struct PyModuleDef module_def = {
    PyModuleDef_HEAD_INIT, "_memalloc", module_doc, 0, /* non-negative size to be able to unload the module */
    module_methods,        NULL,        NULL,       NULL, NULL,
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
