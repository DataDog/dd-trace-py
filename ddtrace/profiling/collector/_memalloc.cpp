#include <mutex>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include "_memalloc_debug.h"
#include "_memalloc_heap.h"
#include "_memalloc_reentrant.h"
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
/* Once hooks are installed, they stay installed for the process lifetime.
 * This flag prevents re-installing hooks on start/stop/start cycles. */
static bool memalloc_hooks_installed = false;
static std::once_flag memalloc_fork_handler_once_flag;

static void
memalloc_free(void* ctx, void* ptr)
{
    PyMemAllocatorEx* alloc = (PyMemAllocatorEx*)ctx;

    if (ptr == NULL)
        return;

#ifdef MEMALLOC_ASSERT_ON_REENTRY
    if (_MEMALLOC_ON_THREAD) {
        _memalloc_abort_free_reentry();
    }
#endif // MEMALLOC_ASSERT_ON_REENTRY

    /* Check if this allocation has a prepended header (sampled allocation).
     * This is a simple 8-byte read + compare — much cheaper than a hashmap lookup. */
    if (memalloc_heap_is_sampled(ptr)) {
        /* Extract the metadata pointer from the header */
        const memalloc_header_t* header =
          reinterpret_cast<const memalloc_header_t*>(static_cast<const char*>(ptr) - MEMALLOC_HEADER_SIZE);
        void* metadata = header->metadata_ptr;

        /* Untrack from the intrusive list and return traceback to pool.
         * metadata may be null if tracking failed (e.g., reentry guard). */
        if (metadata) {
            memalloc_heap_untrack_from_header_no_cpython(metadata);
        }

        /* Clear the signature BEFORE freeing to prevent false positives.
         * After free, the memory may be reused. If a later non-sampled
         * allocation ends up at an address where the old signature lingers,
         * is_sampled() would false-positive and cause a wrong free. */
        void* real_ptr = static_cast<char*>(ptr) - MEMALLOC_HEADER_SIZE;
        memalloc_header_t* free_header = static_cast<memalloc_header_t*>(real_ptr);
        free_header->signature = 0;

        alloc->free(alloc->ctx, real_ptr);
    } else {
        /* Non-sampled allocation: pass through directly */
        alloc->free(alloc->ctx, ptr);
    }
}

static void*
memalloc_alloc(int use_calloc, void* ctx, size_t nelem, size_t elsize)
{
    void* ptr;
    memalloc_context_t* memalloc_ctx = (memalloc_context_t*)ctx;
    size_t total_size = nelem * elsize;

    if (!memalloc_enabled) {
        /* Profiler is stopped but hooks are still installed — pass through */
        if (use_calloc)
            return memalloc_ctx->pymem_allocator_obj.calloc(memalloc_ctx->pymem_allocator_obj.ctx, nelem, elsize);
        else
            return memalloc_ctx->pymem_allocator_obj.malloc(memalloc_ctx->pymem_allocator_obj.ctx, total_size);
    }

    /* Check sampling decision BEFORE allocating so we know whether to
     * request extra space for the header. */
    uint64_t allocated_memory_val = 0;
    bool sampled = memalloc_heap_should_sample_no_cpython(total_size, &allocated_memory_val);

    if (sampled) {
        /* Sampled: allocate size + header, return user_ptr = real_ptr + 16 */
        size_t alloc_size = total_size + MEMALLOC_HEADER_SIZE;

        if (use_calloc) {
            ptr = memalloc_ctx->pymem_allocator_obj.calloc(memalloc_ctx->pymem_allocator_obj.ctx, 1, alloc_size);
        } else {
            ptr = memalloc_ctx->pymem_allocator_obj.malloc(memalloc_ctx->pymem_allocator_obj.ctx, alloc_size);
        }

        if (!ptr)
            return NULL;

        void* user_ptr = static_cast<char*>(ptr) + MEMALLOC_HEADER_SIZE;

        /* Write signature into the header BEFORE tracking, so that if tracking
         * fails (e.g., due to reentry guard), free() still knows this allocation
         * has a header and will free real_ptr instead of user_ptr.
         * Set metadata_ptr to null initially; track will update it on success. */
        memalloc_header_t* header = static_cast<memalloc_header_t*>(ptr);
        header->signature = MEMALLOC_SIGNATURE;
        header->metadata_ptr = nullptr;

        /* Track the allocation — updates metadata_ptr in the header on success */
        memalloc_heap_track_invokes_cpython(
          memalloc_ctx->max_nframe, user_ptr, total_size, allocated_memory_val, memalloc_ctx->domain);

        return user_ptr;
    } else {
        /* Not sampled: allocate normally, no header */
        if (use_calloc)
            ptr = memalloc_ctx->pymem_allocator_obj.calloc(memalloc_ctx->pymem_allocator_obj.ctx, nelem, elsize);
        else
            ptr = memalloc_ctx->pymem_allocator_obj.malloc(memalloc_ctx->pymem_allocator_obj.ctx, total_size);

        return ptr;
    }
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

    if (ptr == NULL) {
        /* realloc(NULL, size) is equivalent to malloc(size) */
        return memalloc_alloc(0, ctx, 1, new_size);
    }

    if (new_size == 0) {
        /* realloc(ptr, 0) is equivalent to free(ptr) */
        memalloc_free(ctx, ptr);
        return NULL;
    }

    bool old_sampled = memalloc_heap_is_sampled(ptr);

    if (!old_sampled) {
        if (!memalloc_enabled) {
            /* Profiler stopped and allocation wasn't sampled — pure pass-through */
            return memalloc_ctx->pymem_allocator_obj.realloc(memalloc_ctx->pymem_allocator_obj.ctx, ptr, new_size);
        }

        /* Check the new sampling decision before allocating so we know whether to
         * add a header.  should_sample advances allocated_memory, so we must NOT
         * call it again via memalloc_alloc — that would double-count the size. */
        uint64_t allocated_memory_val = 0;
        bool new_sampled = memalloc_heap_should_sample_no_cpython(new_size, &allocated_memory_val);

        if (!new_sampled) {
            /* non-sampled → non-sampled (common case): delegate to the underlying
             * realloc directly.  We don't know old_size for non-sampled
             * allocations, so using our own malloc+memcpy+free would risk
             * memcpy reading past the end of the old block. */
            return memalloc_ctx->pymem_allocator_obj.realloc(memalloc_ctx->pymem_allocator_obj.ctx, ptr, new_size);
        }

        /* non-sampled → sampled (rare): must switch to header-prefixed layout.
         * Allocate the new block manually since memalloc_alloc would re-call
         * should_sample_no_cpython and double-count the size. */
        size_t alloc_size = new_size + MEMALLOC_HEADER_SIZE;
        void* real_ptr = memalloc_ctx->pymem_allocator_obj.malloc(memalloc_ctx->pymem_allocator_obj.ctx, alloc_size);
        if (!real_ptr) {
            return NULL;
        }
        void* new_ptr = static_cast<char*>(real_ptr) + MEMALLOC_HEADER_SIZE;

        memalloc_header_t* new_header = static_cast<memalloc_header_t*>(real_ptr);
        new_header->signature = MEMALLOC_SIGNATURE;
        new_header->metadata_ptr = nullptr;

        memalloc_heap_track_invokes_cpython(
          memalloc_ctx->max_nframe, new_ptr, new_size, allocated_memory_val, memalloc_ctx->domain);

        /* Copy old data.  old_size is unknown for non-sampled allocations; copy
         * new_size bytes.  If new_size > old_size this over-reads the old block
         * (UB), but: (a) this transition is rare, (b) callers growing allocations
         * always write new positions before reading, so any garbage bytes beyond
         * old_size are never observed. */
        memcpy(new_ptr, ptr, new_size);

        /* Free the old non-sampled block directly — no header to remove. */
        memalloc_ctx->pymem_allocator_obj.free(memalloc_ctx->pymem_allocator_obj.ctx, ptr);

        return new_ptr;
    }

    /* Old allocation was sampled: use malloc+memcpy+free.
     * We can't use the underlying realloc when the header presence changes
     * (sampled→non-sampled or vice versa) because the data offset differs.
     * Even for sampled→sampled, malloc+memcpy+free is simpler and correct. */

    size_t old_size;
    {
        const memalloc_header_t* header =
          reinterpret_cast<const memalloc_header_t*>(static_cast<const char*>(ptr) - MEMALLOC_HEADER_SIZE);
        traceback_t* old_tb = static_cast<traceback_t*>(header->metadata_ptr);
        if (old_tb) {
            old_size = old_tb->alloc_size;
        } else {
            /* Tracking failed — we don't know the old size. Use new_size. */
            old_size = new_size;
        }
    }

    /* Allocate new block via our alloc (which handles sampling decision) */
    void* new_ptr = memalloc_alloc(0, ctx, 1, new_size);
    if (!new_ptr) {
        return NULL;
    }

    /* Copy data from old to new */
    size_t copy_size = old_size < new_size ? old_size : new_size;
    memcpy(new_ptr, ptr, copy_size);

    /* Free old block via our free (which handles header cleanup) */
    memalloc_free(ctx, ptr);

    return new_ptr;
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
    ddup_start();

    // Register fork handler (once per process)
    std::call_once(memalloc_fork_handler_once_flag,
                   []() { pthread_atfork(nullptr, nullptr, memalloc_heap_postfork_child); });

    char* val = getenv("_DD_MEMALLOC_DEBUG_RNG_SEED");
    if (val) {
        srand(atoi(val));
    }

    long max_nframe;
    long long int heap_sample_size;

    if (!PyArg_ParseTuple(args, "lL", &max_nframe, &heap_sample_size)) {
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

    if (!memalloc_heap_tracker_init_no_cpython((uint32_t)heap_sample_size)) {
        PyErr_SetString(PyExc_RuntimeError, "failed to initialize heap tracker");
        return nullptr;
    }

    /* Install hooks if not already installed. Hooks are permanent once installed
     * because sampled allocations have prepended headers — if we restored the
     * original allocator, free(user_ptr) would pass real_ptr+16 to the original
     * allocator, causing a crash. */
    if (!memalloc_hooks_installed) {
        PyMemAllocatorEx alloc;

        alloc.malloc = memalloc_malloc;
        alloc.calloc = memalloc_calloc;
        alloc.realloc = memalloc_realloc;
        alloc.free = memalloc_free;

        alloc.ctx = &global_memalloc_ctx;

        global_memalloc_ctx.domain = PYMEM_DOMAIN_OBJ;

        PyMem_GetAllocator(PYMEM_DOMAIN_OBJ, &global_memalloc_ctx.pymem_allocator_obj);
        PyMem_SetAllocator(PYMEM_DOMAIN_OBJ, &alloc);

        memalloc_hooks_installed = true;
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

    /* Do NOT restore the original allocator — hooks must stay installed
     * because live sampled allocations have prepended headers. Free/realloc
     * always check the signature, which is a single 8-byte read + compare.
     * Disabling memalloc_enabled causes alloc to pass through without
     * adding headers or sampling. */

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
