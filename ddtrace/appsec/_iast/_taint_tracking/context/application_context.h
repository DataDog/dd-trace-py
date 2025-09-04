// ApplicationContext: ContextVar-indexed array of taint maps
//
// Design overview
// - Python layer stores a ContextVar (IAST_CONTEXT) holding a small integer
//   context_id that indexes into this native-managed array. Each slot holds a
//   shared_ptr<TaintRangeMapType> (a taint map) for a single request/task.
// - This design isolates contexts across threads and asyncio tasks while
//   keeping lookups fast (index-based) and offering a fallback search across
//   all active maps when only a PyObject is available.
// - The array capacity is bounded and configurable via env var
//   DD_IAST_MAX_CONCURRENT_REQUESTS, and clamped to a safe range.
//
// Key operations
// - create_context_array(): returns a free slot index, installing a fresh
//   taint map in that position. Callers typically store this index in
//   IAST_CONTEXT at request start.
// - get_taint_map_by_ctx_id(id): fast-path retrieval for the map in a given
//   slot; returns nullptr if the slot is empty.
// - get_taint_map(PyObject*): scans active maps to locate the one containing
//   the tainted object when no context_id is known (slow path / interop).
// - clear_taint_map(id): clears and frees a specific slot.
// - clear_contexts_array(): clears all slots.
// - capacity(): returns the number of slots provisioned.
//
// Usage lifecycle
// - At request/task start: create_context_array() -> context_id, store in
//   ContextVar. At end: clear_taint_map(context_id).
// - Propagation and aspects can use the fast path by reading context_id from
//   the ContextVar and calling into get_taint_map_by_ctx_id().

#pragma once
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <memory>
#include <optional>
#include <string>
#include <vector>

#include "taint_tracking/taint_range.h"

namespace py = pybind11;

class ApplicationContext
{
  private:
    // Fixed capacity array of context maps
    std::vector<TaintRangeMapTypePtr> contexts_array;
    // Parse and clamp capacity from environment
    static size_t compute_capacity();

  public:
    ApplicationContext();

    // Fast-path: get the taint map for a known context_id (slot index).
    // Returns nullptr if the slot is empty or out of lifecycle.
    TaintRangeMapTypePtr get_taint_map_by_ctx_id(size_t ctx_id);

    // Slow-path: find and return the taint map that contains the given
    // tainted object across all active slots. Returns nullptr if not found or
    // object has no taint ranges.
    TaintRangeMapTypePtr get_taint_map(PyObject* tainted_object);

    // Clear a specific map if present; leaves the slot free for reuse.
    void clear_taint_map(size_t ctx_id);

    // Clear all maps and free all slots.
    void clear_contexts_array();

    // Create a context map for the current request. Returns the index (slot)
    // of the created map when successful. If capacity is saturated, the
    // returned optional may encode an invalid value (implementation detail),
    // and callers should treat that as failure to allocate a slot.
    std::optional<size_t> create_context_array();

    // Introspection helpers
    size_t capacity() const { return contexts_array.size(); }

    // Convenience: retrieve the tainted object directly if present in any
    // active map; nullptr otherwise.
    TaintedObjectPtr get_tainted_object_from_contexts_array(PyObject* tainted_object);
};

extern std::unique_ptr<ApplicationContext> application_context;

void
pyexport_application_context(py::module& m);
