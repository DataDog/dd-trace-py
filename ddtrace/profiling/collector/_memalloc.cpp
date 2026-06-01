#include <atomic>
#include <mutex>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include "_memalloc_code_cache.h"
#include "_memalloc_debug.h"
#include "_memalloc_heap.h"
#include "_memalloc_reentrant.h"
#include "_memalloc_tb.h"
#include "_pymacro.h"

// Ensure profile_state is initialized before creating Sample objects
#include "ddup_interface.hpp"

typedef struct
{
    /* The domain we are tracking */
    PyMemAllocatorDomain domain;
    /* The maximum number of frames collected in stack traces */
    uint16_t max_nframe;

} memalloc_context_t;

/* We only support being started once, so we use a global context for the whole
   module. If we ever want to be started multiple times, we'd need a more
   object-oriented approach and allocate a context per object.
*/
static memalloc_context_t global_memalloc_ctx;
#ifdef _PY312_AND_LATER
static memalloc_context_t global_memalloc_ctx_mem;
#endif // _PY312_AND_LATER

static bool memalloc_enabled = false;
#ifdef _PY312_AND_LATER
/* true only while MEM-domain hooks are installed (between start() with the
 * caller's mem_domain_enabled=true and the next stop()). */
static bool memalloc_mem_installed = false;
#endif // _PY312_AND_LATER
static std::once_flag memalloc_fork_handler_once_flag;

/* Two-slot buffer for atomically publishing the saved (original) allocator.
 *
 * Each start() cycle writes into the slot at index g_saved_alloc_slot (then
 * flips the index) and then atomically publishes the pointer via
 * g_saved_alloc_pub.  Hooks load the pointer atomically, so a hook from
 * cycle N always refers to g_saved_alloc_buf[N%2] while start() for cycle
 * N+1 writes to g_saved_alloc_buf[(N+1)%2].  These are disjoint memory
 * locations, so no data race exists on the struct fields themselves. */
static PyMemAllocatorEx g_saved_alloc_buf[2];
static int g_saved_alloc_slot = 0;
static std::atomic<const PyMemAllocatorEx*> g_saved_alloc_pub{ nullptr };

#ifdef _PY312_AND_LATER
/* MEM-domain saved allocator — same two-slot scheme as OBJ above, kept as
 * an independent buffer + atomic pair so each domain's lifecycle is fully
 * isolated from OBJ. */
static PyMemAllocatorEx g_saved_alloc_mem_buf[2];
static int g_saved_alloc_mem_slot = 0;
static std::atomic<const PyMemAllocatorEx*> g_saved_alloc_mem_pub{ nullptr };
#endif // _PY312_AND_LATER

static void
memalloc_free(void* Py_UNUSED(ctx), void* ptr)
{
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

    /* Load atomically so we see a consistent slot written by start() even if
     * a concurrent restart is in progress.  A NULL guard matches alloc and
     * realloc: leaking is safer than crashing through a torn pointer. */
    const PyMemAllocatorEx* const saved = g_saved_alloc_pub.load(std::memory_order_acquire);
    if (!saved)
        return;
    PyMemAllocatorEx alloc = *saved;
    if (!alloc.free)
        return;
    memalloc_heap_untrack_no_cpython(ptr);
    alloc.free(alloc.ctx, ptr);
}

static void*
memalloc_alloc(int use_calloc, void* ctx, size_t nelem, size_t elsize)
{
    void* ptr;
    memalloc_context_t* memalloc_ctx = (memalloc_context_t*)ctx;

    /* Load the saved allocator atomically.  g_saved_alloc_pub points into a
     * two-slot buffer; start() always writes to the slot that no in-flight
     * hook from the previous cycle is reading, so the struct copy below is
     * data-race-free.  A NULL guard matches realloc and free. */
    const PyMemAllocatorEx* const saved = g_saved_alloc_pub.load(std::memory_order_acquire);
    if (!saved)
        return nullptr;
    PyMemAllocatorEx alloc = *saved;

    if (use_calloc) {
        if (!alloc.calloc)
            return nullptr;
        ptr = alloc.calloc(alloc.ctx, nelem, elsize);
    } else {
        if (!alloc.malloc)
            return nullptr;
        ptr = alloc.malloc(alloc.ctx, nelem * elsize);
    }

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
    /* Load atomically — same two-slot scheme as memalloc_alloc. */
    const PyMemAllocatorEx* const saved = g_saved_alloc_pub.load(std::memory_order_acquire);
    if (!saved)
        return nullptr;
    PyMemAllocatorEx alloc = *saved;
    if (!alloc.realloc)
        return nullptr;
    void* ptr2 = alloc.realloc(alloc.ctx, ptr, new_size);
    // The GIL is held here since we're using PYMEM_DOMAIN_OBJ.
    // TODO(dsn): With Python free-threading, allocators must be thread-safe even for non-RAW domains.
    // We may need to add synchronization here in the future to avoid races between realloc and untrack.
    if (ptr2) {
        memalloc_heap_untrack_no_cpython(ptr);
        memalloc_heap_track_invokes_cpython(memalloc_ctx->max_nframe, ptr2, new_size, memalloc_ctx->domain);
    } else if (new_size == 0 && ptr != NULL) {
        // realloc(ptr, 0) is implementation-defined: some allocators (including
        // glibc) free ptr and return NULL.  In that case ptr is gone and must be
        // untracked so allocs_m doesn't keep a dangling/stale entry forever.
        // When new_size > 0 and ptr2 == NULL the allocation failed; ptr is
        // still valid and must stay tracked, so we only act on new_size == 0.
        memalloc_heap_untrack_no_cpython(ptr);
    }

    return ptr2;
}

#ifdef _PY312_AND_LATER
/* ---------------------------------------------------------------------------
 * PYMEM_DOMAIN_MEM hooks — direct copies of the OBJ hooks above, reading
 * the MEM-domain saved-allocator atomic pair. Kept as separate function
 * definitions (rather than parameterizing hooks) so each function
 * can be reverted independently of the other.
 * --------------------------------------------------------------------------- */

static void
memalloc_free_mem(void* Py_UNUSED(ctx), void* ptr)
{
    if (ptr == NULL)
        return;

#ifdef MEMALLOC_ASSERT_ON_REENTRY
    if (_MEMALLOC_ON_THREAD) {
        _memalloc_abort_free_reentry();
    }
#endif // MEMALLOC_ASSERT_ON_REENTRY

    const PyMemAllocatorEx* const saved = g_saved_alloc_mem_pub.load(std::memory_order_acquire);
    if (!saved)
        return;
    PyMemAllocatorEx alloc = *saved;
    if (!alloc.free)
        return;
    memalloc_heap_untrack_no_cpython(ptr);
    alloc.free(alloc.ctx, ptr);
}

static void*
memalloc_alloc_mem(int use_calloc, void* ctx, size_t nelem, size_t elsize)
{
    void* ptr;
    memalloc_context_t* memalloc_ctx = (memalloc_context_t*)ctx;

    const PyMemAllocatorEx* const saved = g_saved_alloc_mem_pub.load(std::memory_order_acquire);
    if (!saved)
        return nullptr;
    PyMemAllocatorEx alloc = *saved;

    if (use_calloc) {
        if (!alloc.calloc)
            return nullptr;
        ptr = alloc.calloc(alloc.ctx, nelem, elsize);
    } else {
        if (!alloc.malloc)
            return nullptr;
        ptr = alloc.malloc(alloc.ctx, nelem * elsize);
    }

    if (ptr) {
        memalloc_heap_track_invokes_cpython(memalloc_ctx->max_nframe, ptr, nelem * elsize, memalloc_ctx->domain);
    }

    return ptr;
}

static void*
memalloc_malloc_mem(void* ctx, size_t size)
{
    return memalloc_alloc_mem(0, ctx, 1, size);
}

static void*
memalloc_calloc_mem(void* ctx, size_t nelem, size_t elsize)
{
    return memalloc_alloc_mem(1, ctx, nelem, elsize);
}

static void*
memalloc_realloc_mem(void* ctx, void* ptr, size_t new_size)
{
    memalloc_context_t* memalloc_ctx = (memalloc_context_t*)ctx;
    const PyMemAllocatorEx* const saved = g_saved_alloc_mem_pub.load(std::memory_order_acquire);
    if (!saved)
        return nullptr;
    PyMemAllocatorEx alloc = *saved;
    if (!alloc.realloc)
        return nullptr;
    void* ptr2 = alloc.realloc(alloc.ctx, ptr, new_size);
    if (ptr2) {
        memalloc_heap_untrack_no_cpython(ptr);
        memalloc_heap_track_invokes_cpython(memalloc_ctx->max_nframe, ptr2, new_size, memalloc_ctx->domain);
    } else if (new_size == 0 && ptr != NULL) {
        memalloc_heap_untrack_no_cpython(ptr);
    }

    return ptr2;
}
#endif // _PY312_AND_LATER

PyDoc_STRVAR(memalloc_start__doc__,
             "start($module, max_nframe, heap_sample_interval, mem_domain_enabled, code_cache_size)\n"
             "--\n"
             "\n"
             "Start tracing Python memory allocations.\n"
             "\n"
             "Sets the maximum number of frames stored in the traceback of a\n"
             "trace to max_nframe.\n"
             "Sets the average number of bytes allocated between samples to\n"
             "heap_sample_interval.\n"
             "If heap_sample_interval is set to 0, it is disabled entirely.\n"
             "If mem_domain_enabled is true and the Python version supports it\n"
             "(>= 3.12), MEM-domain allocations (PyMem_Malloc/Calloc/Realloc)\n"
             "are tracked in addition to OBJ-domain allocations. This is off\n"
             "by default because MEM-domain interposition adds per-allocation\n"
             "overhead on hot paths (list/dict resize, buffer growth) and can\n"
             "extend the time threads hold Python locks that allocate inside\n"
             "critical sections. Enable it when you need visibility into\n"
             "PyMem_*-only allocations that the OBJ hook does not capture.\n"
             "code_cache_size sets the capacity of the PyCodeObject->function_id\n"
             "cache used during frame walks; see DD_PROFILING_MEMALLOC_CODE_CACHE_SIZE.\n");
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
    int enable_mem_domain;
    unsigned long long code_cache_size;

    /* Store short ints in ints so we're sure they fit */
    if (!PyArg_ParseTuple(args, "lLpK", &max_nframe, &heap_sample_size, &enable_mem_domain, &code_cache_size)) {
        // Don't set an error string, ParseTuple will set it to a TypeError already.
        return nullptr;
    }

    if (max_nframe < 1 || max_nframe > TRACEBACK_MAX_NFRAME) {
        PyErr_Format(PyExc_ValueError, "the number of frames must be in range [1; %u]", TRACEBACK_MAX_NFRAME);
        return nullptr;
    }

    global_memalloc_ctx.max_nframe = (uint16_t)max_nframe;

    if (heap_sample_size < 0 || heap_sample_size > MAX_HEAP_SAMPLE_SIZE) {
        PyErr_Format(PyExc_ValueError, "the heap sample size must be in range [0; %u]", MAX_HEAP_SAMPLE_SIZE);
        return nullptr;
    }

    if (!memalloc_heap_tracker_init_no_cpython((uint32_t)heap_sample_size, (size_t)code_cache_size)) {
        PyErr_SetString(PyExc_RuntimeError, "failed to initialize heap tracker");
        return nullptr;
    }

    PyMemAllocatorEx alloc;

    alloc.malloc = memalloc_malloc;
    alloc.calloc = memalloc_calloc;
    alloc.realloc = memalloc_realloc;
    alloc.free = memalloc_free;

    alloc.ctx = &global_memalloc_ctx;

    global_memalloc_ctx.domain = PYMEM_DOMAIN_OBJ;

    /* Write the saved (original) allocator into whichever slot is NOT
     * currently being read by hooks from the previous cycle, then publish the
     * pointer atomically.  Because start() and the previous stop() hold the
     * GIL sequentially, all in-flight hooks from the prior cycle are already
     * using the *old* slot; writing to the new slot is therefore data-race-free. */
    const int slot = g_saved_alloc_slot;
    g_saved_alloc_slot = 1 - slot;
    PyMem_GetAllocator(PYMEM_DOMAIN_OBJ, &g_saved_alloc_buf[slot]);
    g_saved_alloc_pub.store(&g_saved_alloc_buf[slot], std::memory_order_release);
    PyMem_SetAllocator(PYMEM_DOMAIN_OBJ, &alloc);

#ifdef _PY312_AND_LATER
    if (enable_mem_domain) {
        PyMemAllocatorEx alloc_mem;
        alloc_mem.malloc = memalloc_malloc_mem;
        alloc_mem.calloc = memalloc_calloc_mem;
        alloc_mem.realloc = memalloc_realloc_mem;
        alloc_mem.free = memalloc_free_mem;
        alloc_mem.ctx = &global_memalloc_ctx_mem;

        global_memalloc_ctx_mem.max_nframe = (uint16_t)max_nframe;
        global_memalloc_ctx_mem.domain = PYMEM_DOMAIN_MEM;

        const int mem_slot = g_saved_alloc_mem_slot;
        g_saved_alloc_mem_slot = 1 - mem_slot;
        PyMem_GetAllocator(PYMEM_DOMAIN_MEM, &g_saved_alloc_mem_buf[mem_slot]);
        g_saved_alloc_mem_pub.store(&g_saved_alloc_mem_buf[mem_slot], std::memory_order_release);
        PyMem_SetAllocator(PYMEM_DOMAIN_MEM, &alloc_mem);
        memalloc_mem_installed = true;
    }
#else
    (void)enable_mem_domain; // silence -Wunused-variable on Python < 3.12
#endif // _PY312_AND_LATER

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
     * or memalloc_heap. The higher-level collector deals with this.
     *
     * Load atomically so we see the fully-written slot published by start(). */
    const PyMemAllocatorEx* saved = g_saved_alloc_pub.load(std::memory_order_acquire);
    if (saved) {
        PyMemAllocatorEx restore = *saved;
        PyMem_SetAllocator(PYMEM_DOMAIN_OBJ, &restore);
    }

#ifdef _PY312_AND_LATER
    if (memalloc_mem_installed) {
        const PyMemAllocatorEx* saved_mem = g_saved_alloc_mem_pub.load(std::memory_order_acquire);
        if (saved_mem) {
            PyMemAllocatorEx restore_mem = *saved_mem;
            PyMem_SetAllocator(PYMEM_DOMAIN_MEM, &restore_mem);
        }
        /* Null out so the MEM free hook fast-exits after stop. */
        g_saved_alloc_mem_pub.store(nullptr, std::memory_order_release);
        memalloc_mem_installed = false;
    }
#endif // _PY312_AND_LATER

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

PyDoc_STRVAR(memalloc_code_cache_stats__doc__,
             "code_cache_stats($module, /)\n"
             "--\n"
             "\n"
             "Return a dict with PyCodeObject* -> function_id cache telemetry: "
             "{'hits', 'misses', 'evictions', 'capacity'}. Returns None when the "
             "cache is not initialized.\n");
static PyObject*
memalloc_code_cache_stats(PyObject* Py_UNUSED(module), PyObject* Py_UNUSED(args))
{
    Datadog::CodeFunctionCache* cache = Datadog::CodeFunctionCache::instance;
    if (cache == nullptr) {
        Py_RETURN_NONE;
    }
    return Py_BuildValue("{s:K,s:K,s:K,s:n}",
                         "hits",
                         (unsigned long long)cache->hits(),
                         "misses",
                         (unsigned long long)cache->misses(),
                         "evictions",
                         (unsigned long long)cache->evictions(),
                         "capacity",
                         (Py_ssize_t)cache->capacity());
}

PyDoc_STRVAR(memalloc_code_cache_per_set_stats__doc__,
             "code_cache_per_set_stats($module, /)\n"
             "--\n"
             "\n"
             "Return a list of WAYS_PER_SET+1 ints: histogram[k] is the number of "
             "sets currently holding exactly k entries. Sum equals num_sets. "
             "Diagnostic only -- iterates every set; not for hot paths. Returns "
             "None when the cache is not initialized.\n");
static PyObject*
memalloc_code_cache_per_set_stats(PyObject* Py_UNUSED(module), PyObject* Py_UNUSED(args))
{
    Datadog::CodeFunctionCache* cache = Datadog::CodeFunctionCache::instance;
    if (cache == nullptr) {
        Py_RETURN_NONE;
    }
    auto hist = cache->occupancy_histogram();
    PyObject* list = PyList_New(static_cast<Py_ssize_t>(hist.size()));
    if (list == nullptr) {
        return nullptr;
    }
    for (size_t i = 0; i < hist.size(); ++i) {
        PyObject* v = PyLong_FromUnsignedLongLong(static_cast<unsigned long long>(hist[i]));
        if (v == nullptr) {
            Py_DECREF(list);
            return nullptr;
        }
        PyList_SET_ITEM(list, static_cast<Py_ssize_t>(i), v);
    }
    return list;
}

PyDoc_STRVAR(memalloc_code_cache_reset_counters__doc__,
             "code_cache_reset_counters($module, /)\n"
             "--\n"
             "\n"
             "Zero hits/misses/evictions counters without clearing cache entries.\n");
static PyObject*
memalloc_code_cache_reset_counters(PyObject* Py_UNUSED(module), PyObject* Py_UNUSED(args))
{
    Datadog::CodeFunctionCache* cache = Datadog::CodeFunctionCache::instance;
    if (cache != nullptr) {
        cache->reset_counters();
    }
    Py_RETURN_NONE;
}

PyDoc_STRVAR(memalloc_code_cache_disable__doc__,
             "code_cache_disable($module, /)\n"
             "--\n"
             "\n"
             "Tear down the singleton cache. After this call, frame walks take the "
             "slow path (intern_string x2 + intern_function per frame). For tests "
             "and A/B microbenches only.\n");
static PyObject*
memalloc_code_cache_disable(PyObject* Py_UNUSED(module), PyObject* Py_UNUSED(args))
{
    Datadog::memalloc_code_cache_deinit();
    Py_RETURN_NONE;
}

PyDoc_STRVAR(memalloc_code_cache_enable__doc__,
             "code_cache_enable($module, capacity=1024, /)\n"
             "--\n"
             "\n"
             "(Re-)create the singleton cache with the given capacity. No-op if already\n"
             "enabled. For tests and A/B microbenches only; normal startup uses start().\n");
static PyObject*
memalloc_code_cache_enable(PyObject* Py_UNUSED(module), PyObject* args)
{
    unsigned long long capacity = Datadog::CodeFunctionCache::DEFAULT_CAPACITY;
    if (!PyArg_ParseTuple(args, "|K", &capacity)) {
        return nullptr;
    }
    Datadog::memalloc_code_cache_init((size_t)capacity);
    Py_RETURN_NONE;
}

static PyMethodDef module_methods[] = {
    { "start", (PyCFunction)memalloc_start, METH_VARARGS, memalloc_start__doc__ },
    { "stop", (PyCFunction)memalloc_stop, METH_NOARGS, memalloc_stop__doc__ },
    { "heap", (PyCFunction)memalloc_heap_py, METH_NOARGS, memalloc_heap_py__doc__ },
    { "code_cache_stats", (PyCFunction)memalloc_code_cache_stats, METH_NOARGS, memalloc_code_cache_stats__doc__ },
    { "code_cache_per_set_stats",
      (PyCFunction)memalloc_code_cache_per_set_stats,
      METH_NOARGS,
      memalloc_code_cache_per_set_stats__doc__ },
    { "code_cache_reset_counters",
      (PyCFunction)memalloc_code_cache_reset_counters,
      METH_NOARGS,
      memalloc_code_cache_reset_counters__doc__ },
    { "code_cache_disable", (PyCFunction)memalloc_code_cache_disable, METH_NOARGS, memalloc_code_cache_disable__doc__ },
    { "code_cache_enable", (PyCFunction)memalloc_code_cache_enable, METH_VARARGS, memalloc_code_cache_enable__doc__ },
    /* sentinel */
    { NULL, NULL, 0, NULL }
};

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
