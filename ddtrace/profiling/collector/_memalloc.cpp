#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include "_memalloc_allocation.h"
#include "_memalloc_allocation_profiler.h"
#include "_memalloc_debug.h"
#include "_memalloc_heap_profiler.h"
#include "_memalloc_heap_sample.h"
#include "_memalloc_reentrant.h"
#include "_memalloc_stacktrace.h"
#include "_memalloc_tb.h"
#include "_pymacro.h"

// Ensure profile_state is initialized before creating Sample objects
#include "ddup_interface.hpp"

typedef struct
{
    PyMemAllocatorEx pymem_allocator_obj;
    /* The domain we are tracking */
    PyMemAllocatorDomain domain;
    /* The maximum number of frames collected in stack traces */
    uint16_t max_nframe;

} memalloc_context_t;

/* We only support being started once, so we use a global context for the whole
   module. If we ever want to be started multiple twice, we'd need a more
   object-oriented approach and allocate a context per object.
*/
static memalloc_context_t global_memalloc_ctx;

static bool memalloc_enabled = false;

static void
memalloc_free(void* ctx, void* ptr)
{
    PyMemAllocatorEx* alloc = (PyMemAllocatorEx*)ctx;

    if (ptr == NULL)
        return;

    memalloc_heap_untrack_no_cpython(ptr);

    alloc->free(alloc->ctx, ptr);
}

static void*
memalloc_alloc(int use_calloc, void* ctx, size_t nelem, size_t elsize)
{
    void* ptr;
    memalloc_context_t* memalloc_ctx = (memalloc_context_t*)ctx;
    size_t size = nelem * elsize;

    if (use_calloc)
        ptr = memalloc_ctx->pymem_allocator_obj.calloc(memalloc_ctx->pymem_allocator_obj.ctx, nelem, elsize);
    else
        ptr = memalloc_ctx->pymem_allocator_obj.malloc(memalloc_ctx->pymem_allocator_obj.ctx, size);

    if (ptr) {
        // Allocation profiling - fire-and-forget, independent sampling
        if (allocation_profiler_t::instance) {
            uint64_t allocated_memory_val = 0;
            if (allocation_profiler_t::instance->should_sample_no_cpython(size, &allocated_memory_val)) {
                allocation_profiler_t::instance->track_allocation_invokes_cpython(
                    size, allocated_memory_val, memalloc_ctx->max_nframe);
            }
        }

        // Heap profiling - tracks ptr for free, independent sampling
        if (heap_profiler_t::instance) {
            if (heap_profiler_t::instance->should_sample_no_cpython(size)) {
                heap_profiler_t::instance->track_allocation_invokes_cpython(
                    ptr, size, memalloc_ctx->max_nframe);
            }
        }
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
    // The GIL is held here since we're using PYMEM_DOMAIN_OBJ.
    // TODO(dsn): With Python free-threading, allocators must be thread-safe even for non-RAW domains.
    // We may need to add synchronization here in the future to avoid races between realloc and untrack.
    if (ptr2) {
        // Untrack old heap allocation (if it was tracked)
        if (heap_profiler_t::instance) {
            heap_profiler_t::instance->untrack_no_cpython(ptr);
        }

        // Track as new allocation with independent sampling for both profilers
        // Allocation profiling - fire-and-forget
        if (allocation_profiler_t::instance) {
            uint64_t allocated_memory_val = 0;
            if (allocation_profiler_t::instance->should_sample_no_cpython(new_size, &allocated_memory_val)) {
                allocation_profiler_t::instance->track_allocation_invokes_cpython(
                    new_size, allocated_memory_val, memalloc_ctx->max_nframe);
            }
        }

        // Heap profiling - tracks ptr2 for free
        if (heap_profiler_t::instance) {
            if (heap_profiler_t::instance->should_sample_no_cpython(new_size)) {
                heap_profiler_t::instance->track_allocation_invokes_cpython(
                    ptr2, new_size, memalloc_ctx->max_nframe);
            }
        }
    }

    return ptr2;
}

PyDoc_STRVAR(memalloc_start__doc__,
             "start($module, max_nframe, heap_sample_interval)\n"
             "--\n"
             "\n"
             "Start tracing Python memory allocations.\n"
             "\n"
             "Sets the maximum number of frames stored in the traceback of a\n"
             "trace to max_nframe.\n"
             "Sets the average number of bytes allocated between samples to\n"
             "heap_sample_interval.\n"
             "If heap_sample_interval is set to 0, it is disabled entirely.\n");
static PyObject*
memalloc_start(PyObject* Py_UNUSED(module), PyObject* args)
{
    if (memalloc_enabled) {
        PyErr_SetString(PyExc_RuntimeError, "the memalloc module is already started");
        return NULL;
    }

    // Ensure profile_state is initialized before creating Sample objects
    // This initializes the Sample::profile_state which is required for Sample objects to work correctly
    // ddup_start() uses std::call_once, so it's safe to call multiple times
    ddup_start();

    char* val = getenv("_DD_MEMALLOC_DEBUG_RNG_SEED");
    if (val) {
        /* NB: we don't bother checking whether val is actually a valid integer.
         * Doesn't really matter as long as it's consistent */
        srand(atoi(val));
    }

    long max_nframe;
    long long int heap_sample_size;

    /* Store short ints in ints so we're sure they fit */
    if (!PyArg_ParseTuple(args, "lL", &max_nframe, &heap_sample_size))
        return NULL;

    if (max_nframe < 1 || max_nframe > TRACEBACK_MAX_NFRAME) {
        PyErr_Format(PyExc_ValueError, "the number of frames must be in range [1; %u]", TRACEBACK_MAX_NFRAME);
        return NULL;
    }

    global_memalloc_ctx.max_nframe = (uint16_t)max_nframe;

    if (heap_sample_size < 0 || heap_sample_size > MAX_HEAP_SAMPLE_SIZE) {
        PyErr_Format(PyExc_ValueError, "the heap sample size must be in range [0; %u]", MAX_HEAP_SAMPLE_SIZE);
        return NULL;
    }

    // Initialize shared stacktrace module
    if (!memalloc_stacktrace::init_invokes_cpython())
        return NULL;

    // Initialize allocation sample module
    if (!allocation_sample_t::init_invokes_cpython())
        return NULL;

    // Initialize heap sample module
    if (!heap_sample_t::init_invokes_cpython())
        return NULL;

    // Initialize allocation profiler (fire-and-forget, same sampling rate for now)
    if (!memalloc_allocation_profiler_init_no_cpython((uint32_t)heap_sample_size))
        return NULL;

    // Initialize heap profiler (tracks live allocations, same sampling rate for now)
    if (!memalloc_heap_profiler_init_no_cpython((uint32_t)heap_sample_size))
        return NULL;

    // Keep old traceback init for backward compatibility (will be removed later)
    if (!traceback_t::init_invokes_cpython())
        return NULL;

    PyMemAllocatorEx alloc;

    alloc.malloc = memalloc_malloc;
    alloc.calloc = memalloc_calloc;
    alloc.realloc = memalloc_realloc;
    alloc.free = memalloc_free;

    alloc.ctx = &global_memalloc_ctx;

    global_memalloc_ctx.domain = PYMEM_DOMAIN_OBJ;

    PyMem_GetAllocator(PYMEM_DOMAIN_OBJ, &global_memalloc_ctx.pymem_allocator_obj);
    PyMem_SetAllocator(PYMEM_DOMAIN_OBJ, &alloc);

    memalloc_enabled = true;

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
    if (!memalloc_enabled) {
        PyErr_SetString(PyExc_RuntimeError, "the memalloc module was not started");
        return NULL;
    }

    /* First, uninstall our wrappers. There may still be calls to our wrapper in progress,
     * if they happened to release the GIL.
     * NB: We're assuming here that this is not called concurrently with iter_events
     * or memalloc_heap. The higher-level collector deals with this. */
    PyMem_SetAllocator(PYMEM_DOMAIN_OBJ, &global_memalloc_ctx.pymem_allocator_obj);

    // Deinitialize allocation profiler
    memalloc_allocation_profiler_deinit_no_cpython();

    // Deinitialize heap profiler
    memalloc_heap_profiler_deinit_no_cpython();

    // Deinitialize sample modules
    allocation_sample_t::deinit_invokes_cpython();
    heap_sample_t::deinit_invokes_cpython();

    // Deinitialize shared stacktrace module
    memalloc_stacktrace::deinit_invokes_cpython();

    /* Keep old traceback deinit for backward compatibility (will be removed later) */
    traceback_t::deinit_invokes_cpython();

    memalloc_enabled = false;

    Py_RETURN_NONE;
}

PyDoc_STRVAR(memalloc_heap_py__doc__,
             "heap($module, /)\n"
             "--\n"
             "\n"
             "Export sampled heap allocations to the pprof profile.\n");
static PyObject*
memalloc_heap_py(PyObject* Py_UNUSED(module), PyObject* Py_UNUSED(args))
{
    if (!memalloc_enabled) {
        PyErr_SetString(PyExc_RuntimeError, "the memalloc module was not started");
        return NULL;
    }

    // Export current heap state from the new heap profiler
    memalloc_heap_export_no_cpython();
    Py_RETURN_NONE;
}

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

    return m;
}
