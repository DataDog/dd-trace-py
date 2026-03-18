#include <array>
#include <mutex>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include "_memalloc_debug.h"
#include "_memalloc_heap.h"
#include "_memalloc_reentrant.h"
#include "_memalloc_tb.h"
#include "_memalloc_vm_copy.h"
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
    bool installed;

} memalloc_context_t;

/* We only support being started once, so we use a global context for the whole
   module. If we ever want to be started multiple twice, we'd need a more
   object-oriented approach and allocate a context per object.
*/
static constexpr size_t MEMALLOC_MAX_DOMAINS = 2;
static memalloc_context_t global_memalloc_ctx[MEMALLOC_MAX_DOMAINS];
static size_t global_memalloc_ctx_count = 0;

static bool memalloc_enabled = false;
static std::once_flag memalloc_fork_handler_once_flag;

static inline bool
_env_truthy(const char* val)
{
    if (val == nullptr) {
        return false;
    }
    return strcmp(val, "1") == 0 || strcmp(val, "true") == 0 || strcmp(val, "TRUE") == 0 || strcmp(val, "on") == 0 ||
           strcmp(val, "ON") == 0 || strcmp(val, "yes") == 0 || strcmp(val, "YES") == 0;
}

static inline bool
memalloc_track_all_domains_enabled(void)
{
    const char* val = getenv("DD_PROFILING_MEMALLOC_TRACK_ALL_DOMAINS");
    if (val == nullptr) {
#ifdef MEMALLOC_TRACK_ALL_DOMAINS_DEFAULT
        return true;
#else
        return false;
#endif
    }
    if (strcmp(val, "0") == 0 || strcmp(val, "false") == 0 || strcmp(val, "FALSE") == 0 || strcmp(val, "off") == 0 ||
        strcmp(val, "OFF") == 0 || strcmp(val, "no") == 0 || strcmp(val, "NO") == 0) {
        return false;
    }
    return _env_truthy(val);
}

static void
memalloc_free(void* ctx, void* ptr)
{
    memalloc_context_t* memalloc_ctx = (memalloc_context_t*)ctx;
    PyMemAllocatorEx* alloc = &memalloc_ctx->pymem_allocator_obj;

    if (ptr == NULL)
        return;

#ifdef MEMALLOC_ASSERT_ON_REENTRY
    /* Abort in test builds if we're re-entering from the malloc hook.
     * In production we can't abort or skip untrack (skipping would leak
     * heap tracker entries), so we just let it proceed — direct struct
     * access frame walking avoids calling CPython APIs that could free and is thus safe. */
    if (_MEMALLOC_ON_THREAD) {
        _memalloc_abort_free_reentry();
    }
#endif // MEMALLOC_ASSERT_ON_REENTRY

    memalloc_heap_untrack_no_cpython(ptr, memalloc_ctx->domain);
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

    if (ptr) {
        memalloc_heap_track_invokes_cpython(memalloc_ctx->max_nframe, ptr, nelem * elsize, memalloc_ctx->domain);
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
        memalloc_heap_untrack_no_cpython(ptr, memalloc_ctx->domain);
        memalloc_heap_track_invokes_cpython(memalloc_ctx->max_nframe, ptr2, new_size, memalloc_ctx->domain);
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
        return nullptr;
    }

    // Ensure profile_state is initialized before creating Sample objects
    // This initializes the Sample::profile_state which is required for Sample objects to work correctly
    // ddup_start() uses std::call_once, so it's safe to call multiple times
    // ddup_start also registers fork handlers for various components, so if
    // any of memalloc's states refer to states that are reset after fork,
    // memalloc also has to clear its state after fork via below fork handler.
    ddup_start();

    // Register fork handler
    // Mainly to clear the heap tracker state before running any Python code,
    // otherwise it can lead to undefined behaviors and/or crashes, ref:
    // incident-48649.
    // We use std::call_once as registered fork handlers persist after fork, and
    // we want to ensure that the fork handlers are registered only once per
    // process, even when the memory profiler is restarted after fork.
    std::call_once(memalloc_fork_handler_once_flag,
                   []() { pthread_atfork(nullptr, nullptr, memalloc_heap_postfork_child); });

    char* val = getenv("_DD_MEMALLOC_DEBUG_RNG_SEED");
    if (val) {
        /* NB: we don't bother checking whether val is actually a valid integer.
         * Doesn't really matter as long as it's consistent */
        srand(atoi(val));
    }

    long max_nframe;
    long long int heap_sample_size;

    /* Store short ints in ints so we're sure they fit */
    if (!PyArg_ParseTuple(args, "lL", &max_nframe, &heap_sample_size)) {
        // Don't set an error string, ParseTuple will set it to a TypeError already.
        return nullptr;
    }

    if (max_nframe < 1 || max_nframe > TRACEBACK_MAX_NFRAME) {
        PyErr_Format(PyExc_ValueError, "the number of frames must be in range [1; %u]", TRACEBACK_MAX_NFRAME);
        return nullptr;
    }

    if (heap_sample_size < 0 || heap_sample_size > MAX_HEAP_SAMPLE_SIZE) {
        PyErr_Format(PyExc_ValueError, "the heap sample size must be in range [0; %u]", MAX_HEAP_SAMPLE_SIZE);
        return nullptr;
    }

    if (!memalloc_heap_tracker_init_no_cpython((uint32_t)heap_sample_size)) {
        PyErr_SetString(PyExc_RuntimeError, "failed to initialize heap tracker");
        return nullptr;
    }
    _set_pid(getpid());

    PyMemAllocatorEx alloc;

    alloc.malloc = memalloc_malloc;
    alloc.calloc = memalloc_calloc;
    alloc.realloc = memalloc_realloc;
    alloc.free = memalloc_free;
    const bool all_domains_enabled = memalloc_track_all_domains_enabled();
    const std::array<PyMemAllocatorDomain, MEMALLOC_MAX_DOMAINS> domains = { PYMEM_DOMAIN_MEM, PYMEM_DOMAIN_RAW };
    global_memalloc_ctx_count = all_domains_enabled ? MEMALLOC_MAX_DOMAINS : 1;

    for (size_t i = 0; i < global_memalloc_ctx_count; ++i) {
        memalloc_context_t* ctx = &global_memalloc_ctx[i];
        ctx->domain = domains[i];
        ctx->max_nframe = (uint16_t)max_nframe;
        ctx->installed = false;
        PyMem_GetAllocator(ctx->domain, &ctx->pymem_allocator_obj);
        alloc.ctx = ctx;
        PyMem_SetAllocator(ctx->domain, &alloc);
        ctx->installed = true;
    }

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
    for (size_t i = global_memalloc_ctx_count; i > 0; --i) {
        memalloc_context_t* ctx = &global_memalloc_ctx[i - 1];
        if (ctx->installed) {
            PyMem_SetAllocator(ctx->domain, &ctx->pymem_allocator_obj);
            ctx->installed = false;
        }
    }
    global_memalloc_ctx_count = 0;

    memalloc_heap_tracker_deinit_no_cpython();

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

    memalloc_heap_no_cpython();
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
