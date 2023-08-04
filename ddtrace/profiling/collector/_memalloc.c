#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include "_memalloc_heap.h"
#include "_memalloc_reentrant.h"
#include "_memalloc_tb.h"
#include "_pymacro.h"
#include "_utils.h"

typedef struct
{
    PyMemAllocatorEx pymem_allocator_obj;
    /* The domain we are tracking */
    PyMemAllocatorDomain domain;
    /* The maximum number of events for allocation tracking */
    uint16_t max_events;
    /* The maximum number of frames collected in stack traces */
    uint16_t max_nframe;
} memalloc_context_t;

/* We only support being started once, so we use a global context for the whole
   module. If we ever want to be started multiple twice, we'd need a more
   object-oriented approach and allocate a context per object.
*/
static memalloc_context_t global_memalloc_ctx;

/* Allocation tracker */
typedef struct
{
    /* List of traceback */
    traceback_array_t allocs;
    /* Total number of allocations */
    uint64_t alloc_count;
} alloc_tracker_t;

/* A string containing "object" */
static PyObject* object_string = NULL;

#define ALLOC_TRACKER_MAX_COUNT UINT64_MAX

static alloc_tracker_t* global_alloc_tracker;

static void
memalloc_add_event(memalloc_context_t* ctx, void* ptr, size_t size)
{
    /* Do not overflow; just ignore the new events if we ever reach that point */
    if (global_alloc_tracker->alloc_count >= ALLOC_TRACKER_MAX_COUNT)
        return;

    global_alloc_tracker->alloc_count++;

    /* Avoid loops */
    if (memalloc_get_reentrant())
        return;

    /* Determine if we can capture or if we need to sample */
    if (global_alloc_tracker->allocs.count < ctx->max_events) {
        /* set a barrier so we don't loop as getting a traceback allocates memory */
        memalloc_set_reentrant(true);
        /* Buffer is not full, fill it */
        traceback_t* tb = memalloc_get_traceback(ctx->max_nframe, ptr, size, ctx->domain);
        memalloc_set_reentrant(false);
        if (tb)
            traceback_array_append(&global_alloc_tracker->allocs, tb);
    } else {
        /* Sampling mode using a reservoir sampling algorithm: replace a random
         * traceback with this one */
        uint64_t r = random_range(global_alloc_tracker->alloc_count);

        if (r < ctx->max_events) {
            /* set a barrier so we don't loop as getting a traceback allocates memory */
            memalloc_set_reentrant(true);
            /* Replace a random traceback with this one */
            traceback_t* tb = memalloc_get_traceback(ctx->max_nframe, ptr, size, ctx->domain);
            memalloc_set_reentrant(false);
            if (tb) {
                traceback_free(global_alloc_tracker->allocs.tab[r]);
                global_alloc_tracker->allocs.tab[r] = tb;
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

    memalloc_heap_untrack(ptr);

    alloc->free(alloc->ctx, ptr);
}

#ifdef _PY37_AND_LATER
Py_tss_t memalloc_reentrant_key = Py_tss_NEEDS_INIT;
#else
int memalloc_reentrant_key = -1;
#endif

static void*
memalloc_alloc(int use_calloc, void* ctx, size_t nelem, size_t elsize)
{
    void* ptr;
    memalloc_context_t* memalloc_ctx = (memalloc_context_t*)ctx;

    if (use_calloc)
        ptr = memalloc_ctx->pymem_allocator_obj.calloc(memalloc_ctx->pymem_allocator_obj.ctx, nelem, elsize);
    else
        ptr = memalloc_ctx->pymem_allocator_obj.malloc(memalloc_ctx->pymem_allocator_obj.ctx, nelem * elsize);

    if (ptr) {
        memalloc_add_event(memalloc_ctx, ptr, nelem * elsize);
        memalloc_heap_track(memalloc_ctx->max_nframe, ptr, nelem * elsize, memalloc_ctx->domain);
    }

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

    if (ptr2) {
        memalloc_add_event(memalloc_ctx, ptr2, new_size);
        memalloc_heap_untrack(ptr);
        memalloc_heap_track(memalloc_ctx->max_nframe, ptr2, new_size, memalloc_ctx->domain);
    }

    return ptr2;
}

static alloc_tracker_t*
alloc_tracker_new()
{
    alloc_tracker_t* alloc_tracker = PyMem_RawMalloc(sizeof(alloc_tracker_t));
    alloc_tracker->alloc_count = 0;
    traceback_array_init(&alloc_tracker->allocs);
    return alloc_tracker;
}

static void
alloc_tracker_free(alloc_tracker_t* alloc_tracker)
{
    traceback_array_wipe(&alloc_tracker->allocs);
    PyMem_RawFree(alloc_tracker);
}

PyDoc_STRVAR(memalloc_start__doc__,
             "start($module, max_nframe, max_events, heap_sample_size)\n"
             "--\n"
             "\n"
             "Start tracing Python memory allocations.\n"
             "\n"
             "Sets the maximum number of frames stored in the traceback of a\n"
             "trace to max_nframe and the maximum number of events to max_events.\n"
             "Set heap_sample_size to the granularity of the heap profiler, in bytes.\n"
             "If heap_sample_size is set to 0, it is disabled entirely.\n");
static PyObject*
memalloc_start(PyObject* Py_UNUSED(module), PyObject* args)
{
    if (global_alloc_tracker) {
        PyErr_SetString(PyExc_RuntimeError, "the memalloc module is already started");
        return NULL;
    }

    long max_nframe, max_events;
    long long int heap_sample_size;

    /* Store short ints in ints so we're sure they fit */
    if (!PyArg_ParseTuple(args, "llL", &max_nframe, &max_events, &heap_sample_size))
        return NULL;

    if (max_nframe < 1 || max_nframe > TRACEBACK_MAX_NFRAME) {
        PyErr_Format(PyExc_ValueError, "the number of frames must be in range [1; %lu]", TRACEBACK_MAX_NFRAME);
        return NULL;
    }

    global_memalloc_ctx.max_nframe = (uint16_t)max_nframe;

    if (max_events < 1 || max_events > TRACEBACK_ARRAY_MAX_COUNT) {
        PyErr_Format(PyExc_ValueError, "the number of events must be in range [1; %lu]", TRACEBACK_ARRAY_MAX_COUNT);
        return NULL;
    }

    global_memalloc_ctx.max_events = (uint16_t)max_events;

    if (heap_sample_size < 0 || heap_sample_size > MAX_HEAP_SAMPLE_SIZE) {
        PyErr_Format(PyExc_ValueError, "the heap sample size must be in range [0; %lu]", MAX_HEAP_SAMPLE_SIZE);
        return NULL;
    }

    if (memalloc_tb_init(global_memalloc_ctx.max_nframe) < 0)
        return NULL;

    if (object_string == NULL) {
        object_string = PyUnicode_FromString("object");
        if (object_string == NULL)
            return NULL;
        PyUnicode_InternInPlace(&object_string);
    }

    memalloc_heap_tracker_init((uint32_t)heap_sample_size);

    PyMemAllocatorEx alloc;

    alloc.malloc = memalloc_malloc;
    alloc.calloc = memalloc_calloc;
    alloc.realloc = memalloc_realloc;
    alloc.free = memalloc_free;

    alloc.ctx = &global_memalloc_ctx;

    global_memalloc_ctx.domain = PYMEM_DOMAIN_OBJ;

    global_alloc_tracker = alloc_tracker_new();

    PyMem_GetAllocator(PYMEM_DOMAIN_OBJ, &global_memalloc_ctx.pymem_allocator_obj);
    PyMem_SetAllocator(PYMEM_DOMAIN_OBJ, &alloc);

    Py_RETURN_NONE;
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
    if (!global_alloc_tracker) {
        PyErr_SetString(PyExc_RuntimeError, "the memalloc module was not started");
        return NULL;
    }

    PyMem_SetAllocator(PYMEM_DOMAIN_OBJ, &global_memalloc_ctx.pymem_allocator_obj);
    memalloc_tb_deinit();
    alloc_tracker_free(global_alloc_tracker);
    global_alloc_tracker = NULL;

    memalloc_heap_tracker_deinit();

    Py_RETURN_NONE;
}

PyDoc_STRVAR(memalloc_heap_py__doc__,
             "heap($module, /)\n"
             "--\n"
             "\n"
             "Get the sampled heap representation.\n");
static PyObject*
memalloc_heap_py(PyObject* Py_UNUSED(module), PyObject* Py_UNUSED(args))
{
    if (!global_alloc_tracker) {
        PyErr_SetString(PyExc_RuntimeError, "the memalloc module was not started");
        return NULL;
    }

    return memalloc_heap();
}

typedef struct
{
    PyObject_HEAD alloc_tracker_t* alloc_tracker;
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
    if (!global_alloc_tracker) {
        PyErr_SetString(PyExc_RuntimeError, "the memalloc module was not started");
        return NULL;
    }

    IterEventsState* iestate = (IterEventsState*)type->tp_alloc(type, 0);
    if (!iestate)
        return NULL;

    iestate->alloc_tracker = global_alloc_tracker;
    /* reset the current traceback list */
    global_alloc_tracker = alloc_tracker_new();
    iestate->seq_index = 0;

    PyObject* iter_and_count = PyTuple_New(3);
    PyTuple_SET_ITEM(iter_and_count, 0, (PyObject*)iestate);
    PyTuple_SET_ITEM(iter_and_count, 1, PyLong_FromUnsignedLong(iestate->alloc_tracker->allocs.count));
    PyTuple_SET_ITEM(iter_and_count, 2, PyLong_FromUnsignedLongLong(iestate->alloc_tracker->alloc_count));

    return iter_and_count;
}

static void
iterevents_dealloc(IterEventsState* iestate)
{
    alloc_tracker_free(iestate->alloc_tracker);
    Py_TYPE(iestate)->tp_free(iestate);
}

static PyObject*
iterevents_next(IterEventsState* iestate)
{
    if (iestate->seq_index < iestate->alloc_tracker->allocs.count) {
        traceback_t* tb = iestate->alloc_tracker->allocs.tab[iestate->seq_index];
        iestate->seq_index++;

        PyObject* tb_size_domain = PyTuple_New(3);
        PyTuple_SET_ITEM(tb_size_domain, 0, traceback_to_tuple(tb));
        PyTuple_SET_ITEM(tb_size_domain, 1, PyLong_FromSize_t(tb->size));

        /* Domain name */
        if (tb->domain == PYMEM_DOMAIN_OBJ) {
            PyTuple_SET_ITEM(tb_size_domain, 2, object_string);
            Py_INCREF(object_string);
        } else {
            PyTuple_SET_ITEM(tb_size_domain, 2, Py_None);
            Py_INCREF(Py_None);
        }

        return tb_size_domain;
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
                                        { "heap", (PyCFunction)memalloc_heap_py, METH_NOARGS, memalloc_heap_py__doc__ },
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

    // Initialize the DDFrame namedtuple class
    // Do this early so we don't have complicated cleanup
    if (!memalloc_ddframe_class_init()) {
        return NULL;
    }

#ifdef _PY37_AND_LATER
    if (PyThread_tss_create(&memalloc_reentrant_key) != 0) {
#else
    memalloc_reentrant_key = PyThread_create_key();
    if (memalloc_reentrant_key == -1) {
#endif
#ifdef MS_WINDOWS
        PyErr_SetFromWindowsErr(0);
#else
        PyErr_SetFromErrno(PyExc_OSError);
#endif
        return NULL;
    }

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
