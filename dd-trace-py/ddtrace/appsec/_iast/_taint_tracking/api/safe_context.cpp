#include "api/safe_context.h"

// ============================================================================
// Safe wrapper functions for global pointers
// ============================================================================
// These functions centralize null checks for taint_engine_context and
// initializer to prevent segmentation faults when called before
// initialize_native_state(). All aspect functions should use these wrappers
// instead of accessing the globals directly.
TaintedObjectMapTypePtr
safe_get_tainted_object_map(PyObject* tainted_object)
{
    if (!taint_engine_context) {
        return nullptr;
    }
    return taint_engine_context->get_tainted_object_map(tainted_object);
}

TaintedObjectMapTypePtr
safe_get_tainted_object_map_from_list_of_pyobjects(const std::vector<PyObject*>& objects)
{
    if (!taint_engine_context) {
        return nullptr;
    }
    return taint_engine_context->get_tainted_object_map_from_list_of_pyobjects(objects);
}

TaintedObjectMapTypePtr
safe_get_tainted_object_map_by_ctx_id(size_t ctx_id)
{
    if (!taint_engine_context) {
        return nullptr;
    }
    return taint_engine_context->get_tainted_object_map_by_ctx_id(ctx_id);
}
