/**
 * IAST Native Module
 * This C++ module contains IAST propagation features:
 * - Taint tracking: propagation of a tainted variable.
 * - Aspects: common string operations are replaced by functions that propagate the taint variables.
 * - Taint ranges: Information related to tainted values.
 */
#include <memory>
#include <pthread.h>
#include <pybind11/pybind11.h>

#include "aspects/aspect_extend.h"
#include "aspects/aspect_index.h"
#include "aspects/aspect_join.h"
#include "aspects/aspect_modulo.h"
#include "aspects/aspect_operator_add.h"
#include "aspects/aspect_slice.h"
#include "aspects/aspect_str.h"
#include "aspects/aspects_exports.h"
#include "constants.h"
#include "context/_taint_engine_context.h"
#include "taint_tracking/source.h"
#include "taint_tracking/taint_tracking.h"
#include "taint_tracking/tainted_object.h"
#include "tainted_ops/tainted_ops.h"
#include "utils/generic_utils.h"

#define PY_MODULE_NAME_ASPECTS                                                                                         \
    PY_MODULE_NAME "."                                                                                                 \
                   "aspects"

using namespace pybind11::literals;
namespace py = pybind11;

static PyMethodDef AspectsMethods[] = {
    { "add_aspect", ((PyCFunction)api_add_aspect), METH_FASTCALL, "aspect add" },
    { "str_aspect", ((PyCFunction)api_str_aspect), METH_FASTCALL | METH_KEYWORDS, "aspect str" },
    { "add_inplace_aspect", ((PyCFunction)api_add_inplace_aspect), METH_FASTCALL, "aspect add" },
    { "extend_aspect", ((PyCFunction)api_extend_aspect), METH_FASTCALL, "aspect extend" },
    { "index_aspect", ((PyCFunction)api_index_aspect), METH_FASTCALL, "aspect index" },
    { "join_aspect", ((PyCFunction)api_join_aspect), METH_FASTCALL, "aspect join" },
    { "slice_aspect", ((PyCFunction)api_slice_aspect), METH_FASTCALL, "aspect slice" },
    { "modulo_aspect", ((PyCFunction)api_modulo_aspect), METH_FASTCALL, "aspect modulo" },
    { nullptr, nullptr, 0, nullptr }
};

// Mark the module as used to prevent it from being stripped.
static struct PyModuleDef aspects __attribute__((used)) = { PyModuleDef_HEAD_INIT,
                                                            .m_name = PY_MODULE_NAME_ASPECTS,
                                                            .m_doc = "Taint tracking Aspects",
                                                            .m_size = -1,
                                                            .m_methods = AspectsMethods };

static PyMethodDef OpsMethods[] = {
    { "new_pyobject_id", (PyCFunction)api_new_pyobject_id, METH_FASTCALL, "new pyobject id" },
    { "taint_pyobject", ((PyCFunction)api_taint_pyobject), METH_FASTCALL, "api_taint_pyobject" },
    { nullptr, nullptr, 0, nullptr }
};

// Mark the module as used to prevent it from being stripped.
static struct PyModuleDef ops __attribute__((used)) = { PyModuleDef_HEAD_INIT,
                                                        .m_name = PY_MODULE_NAME_ASPECTS,
                                                        .m_doc = "Taint tracking operations",
                                                        .m_size = -1,
                                                        .m_methods = OpsMethods };

/**
 * Initialize or reinitialize the native IAST global state.
 *
 * This creates fresh instances of the global singletons:
 * - initializer: manages memory pools for taint ranges and objects
 * - taint_engine_context: manages per-request taint maps
 *
 * IMPORTANT: This function is idempotent. If already initialized, it does nothing.
 * This ensures that calling it multiple times doesn't destroy existing taint state.
 *
 * Can be called:
 * - At module load time (automatic)
 * - From Python explicitly (for manual control)
 * - Multiple times safely (idempotent)
 */
static void
initialize_native_state()
{
    // Idempotent: only initialize if not already initialized
    // This preserves existing taint state when called multiple times
    if (initializer && taint_engine_context) {
        return; // Already initialized, nothing to do
    }

    // Create fresh instances of global singletons
    initializer = make_unique<Initializer>();
    taint_engine_context = make_unique<TaintEngineContext>();
}

/**
 * Reset native IAST state after fork.
 *
 * This function safely resets all C++ global state that may have been
 * inherited from the parent process during fork. It:
 *
 * 1. Clears all taint maps (they contain stale PyObject pointers)
 * 2. Resets the taint_engine_context (recreates the context array)
 * 3. Resets the initializer (recreates memory pools)
 *
 * This prevents crashes from:
 * - Dereferencing stale PyObject pointers from parent
 * - Using corrupted shared_ptr control blocks
 * - Accessing invalid memory in std::vector/map internals
 *
 * CRITICAL IMPLEMENTATION DETAIL: unique_ptr.release() vs unique_ptr.reset()
 * -------------------------------------------------------------------------
 * This function uses unique_ptr::release() instead of unique_ptr::reset() for
 * a specific technical reason:
 *
 * - unique_ptr::reset(): Deletes the managed object by calling its destructor,
 *   then sets the pointer to nullptr. The destructor will try to clean up all
 *   resources including PyObject pointers.
 *
 * - unique_ptr::release(): Returns the raw pointer and releases ownership
 *   WITHOUT calling the destructor. The pointer becomes the caller's
 *   responsibility, but we intentionally leak it.
 *
 * WHY WE USE release() (intentional memory leak):
 * -----------------------------------------------
 * After fork, the child process inherits copies of the parent's C++ objects
 * via copy-on-write. These objects contain PyObject pointers that reference
 * Python objects in the PARENT process's memory space. These pointers are
 * now INVALID in the child process because:
 * 1. Python's interpreter state is separate in the child
 * 2. The PyObject addresses point to the parent's address space
 * 3. Accessing them causes segmentation faults
 *
 * If we called .reset() (destructor path):
 * - The TaintEngineContext destructor would iterate over all taint maps
 * - It would try to decrement refcounts on PyObject pointers
 * - This would access invalid memory → SEGFAULT
 *
 * By calling .release() (leak path):
 * - We abandon the old objects without cleanup
 * - No destructors run, so no invalid PyObject pointers are accessed
 * - The memory remains allocated but unused (leaked)
 *
 * WHY THIS LEAK IS ACCEPTABLE:
 * ---------------------------
 * 1. Child process lifetime: These child processes are ephemeral (short-lived
 *    workers or test processes) and will exit soon, releasing all memory
 * 2. Memory was COW: The leaked memory was copy-on-write from the parent, so
 *    it's a small cost compared to the parent's memory footprint
 * 3. Alternative is crash: The only alternative is a guaranteed segfault,
 *    making the leak the lesser evil
 * 4. One-time cost: This leak happens exactly once per child process at fork
 *    time, not repeatedly
 *
 * Trade-off: Small one-time memory leak vs guaranteed segmentation fault
 *
 * Called automatically via pthread_atfork in child processes.
 */
static void
reset_native_state_after_fork()
{
    // Important: We're in the child process after fork.
    // The Python GIL is held by the forking thread in the child.

    // WARNING: The inherited context contains PyObject pointers from the PARENT process.
    // These pointers are now invalid in the child and must not be dereferenced.
    //
    // CRITICAL: We must NOT call destructors on the inherited objects because they will
    // try to clean up PyObject pointers that are invalid in the child process.
    // Instead, we intentionally "leak" the old objects by abandoning them without cleanup.
    // This is safe because:
    // 1. We're in a child process that will exit eventually
    // 2. The memory was copy-on-write from the parent
    // 3. Calling destructors would cause segfaults

    // Step 1: Abandon the old pointers WITHOUT calling destructors
    // Using release() transfers ownership and returns the raw pointer without cleanup
    // We intentionally leak these objects to avoid segfaults from destructor cleanup
    if (taint_engine_context) {
        taint_engine_context->clear_tainted_object_map();
        (void)taint_engine_context.release(); // Leak the old object
    }
    if (initializer) {
        (void)initializer.release(); // Leak the old object
    }

    // Step 2: Recreate fresh instances
    initialize_native_state();
}

/**
 * This function initializes the native module.
 */
PYBIND11_MODULE(_native, m)
{
    // Register pthread_atfork handler to reset state in child processes
    // This provides automatic fork-safety:
    // - prepare: NULL (nothing to do before fork in parent)
    // - parent: NULL (nothing to do after fork in parent)
    // - child: reset_native_state_after_fork (reset globals in child)
    pthread_atfork(nullptr, nullptr, reset_native_state_after_fork);

    // Create a atexit callback to cleanup the Initializer before the interpreter finishes
    auto atexit_register = safe_import("atexit", "register");
    atexit_register(py::cpp_function([]() {
        // During interpreter shutdown (esp. with gevent), heavy cleanup can
        // trigger refcounting or Python API calls without a valid runtime.
        // If gevent monkey-patching is active, skip setting the shutdown flag
        // because it interferes with greenlet scheduling at exit.

        bool gevent_active = false;
        try {
            auto is_patched = safe_import("gevent.monkey", "is_module_patched");
            gevent_active =
              asbool(is_patched("threading")) || asbool(is_patched("socket")) || asbool(is_patched("ssl"));
        } catch (const py::error_already_set&) {
            PyErr_Clear();
        }

        if (!gevent_active) {
            py::gil_scoped_acquire gil; // safe to touch Python-adjacent state
            TaintEngineContext::set_shutting_down(true);
        }

        initializer.reset();
        if (taint_engine_context) {
            taint_engine_context->clear_all_request_context_slots();
            taint_engine_context.reset();
        }
    }));

    m.doc() = "Native Python module";
    py::module m_appctx = pyexport_m_taint_engine_context(m);
    pyexport_m_taint_tracking(m);

    pyexport_m_aspect_helpers(m);

    // Export fork-safety functions
    m.def("reset_native_state",
          &reset_native_state_after_fork,
          "Reset native IAST state after fork. "
          "This recreates all global C++ singletons with fresh state, "
          "clearing any inherited PyObject pointers or corrupted memory from parent process.");

    m.def("initialize_native_state",
          &initialize_native_state,
          "Explicitly initialize native IAST state. "
          "Normally called automatically at module load, but can be called manually "
          "from Python for explicit initialization control.");

    // Export testing utilities
    m.def("reset_taint_range_limit_cache",
          &reset_taint_range_limit_cache,
          "Reset the cached taint range limit for testing purposes. "
          "This forces get_taint_range_limit() to re-read DD_IAST_MAX_RANGE_COUNT environment variable.");

    m.def("reset_source_truncation_cache",
          &reset_source_truncation_cache,
          "Reset the cached source truncation length for testing purposes. "
          "This forces get_source_truncation_max_length() to re-read DD_IAST_TRUNCATION_MAX_VALUE_LENGTH environment "
          "variable.");

    // Note: the order of these definitions matter. For example,
    // stacktrace_element definitions must be before the ones of the
    // classes inheriting from it.
    PyObject* hm_aspects = PyModule_Create(&aspects);
    m.add_object("aspects", hm_aspects);

    PyObject* hm_ops = PyModule_Create(&ops);
    m.add_object("ops", hm_ops);
}
