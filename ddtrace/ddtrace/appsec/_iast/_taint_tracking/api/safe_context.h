#pragma once
#include "context/taint_engine_context.h"

// Safe wrapper functions that check for null before accessing global pointers
// These functions centralize null checks to prevent segmentation faults when
// called before initialize_native_state()

/**
 * @brief Safely get tainted object map for a PyObject.
 * @param tainted_object The Python object to look up
 * @return TaintedObjectMapTypePtr or nullptr if not initialized or not found
 */
TaintedObjectMapTypePtr
safe_get_tainted_object_map(PyObject* tainted_object);

/**
 * @brief Safely get tainted object map by context ID.
 * @param ctx_id The context ID to look up
 * @return TaintedObjectMapTypePtr or nullptr if not initialized or not found
 */
TaintedObjectMapTypePtr
safe_get_tainted_object_map_by_ctx_id(size_t ctx_id);

/**
 * @brief Safely get tainted object map from a list of PyObjects.
 * @param objects Vector of PyObject pointers to search
 * @return TaintedObjectMapTypePtr or nullptr if not initialized or not found
 */
TaintedObjectMapTypePtr
safe_get_tainted_object_map_from_list_of_pyobjects(const std::vector<PyObject*>& objects);
