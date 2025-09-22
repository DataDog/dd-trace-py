// TaintEngineContext: ContextVar-indexed array of taint maps
//
// Design overview
// - Python layer stores a ContextVar (IAST_CONTEXT) holding a small integer
//   context_id that indexes into this native-managed array. Each slot holds a
//   shared_ptr<TaintedObjectMapType> (a taint map) for a single request/task.
// - This design isolates contexts across threads and asyncio tasks while
//   keeping lookups fast (index-based) and offering a fallback search across
//   all active maps when only a PyObject is available.
// - The array capacity is bounded and configurable via env var
//   DD_IAST_MAX_CONCURRENT_REQUESTS, and clamped to a safe range.
//
// Key operations
// - start_request_context(): returns a free slot index, installing a fresh
//   taint map in that position. Callers typically store this index in
//   IAST_CONTEXT at request start.
// - get_tainted_object_map_by_ctx_id(id): fast-path retrieval for the map in a given
//   slot; returns nullptr if the slot is empty.
// - get_tainted_object_map(PyObject*): scans active maps to locate the one containing
//   the tainted object when no context_id is known (slow path / interop).
// - finish_request_context(id): clears and frees a specific slot.
// - clear_all_request_context_slots(): clears all slots.
// - capacity(): returns the number of slots provisioned.
//
// Usage lifecycle
// - At request/task start: start_request_context() -> context_id, store in
//   ContextVar. At end: finish_request_context(context_id).
// - Propagation and aspects can use the fast path by reading context_id from
//   the ContextVar and calling into get_tainted_object_map_by_ctx_id().
//
#pragma once
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <atomic>
#include <memory>
#include <optional>
#include <string>
#include <vector>

#include "taint_tracking/taint_range.h"

namespace py = pybind11;

class TaintEngineContext
{
  private:
    // Fixed capacity array of context maps
    std::vector<TaintedObjectMapTypePtr> request_context_slots;
    // Parse and clamp capacity from environment
    static size_t assign_request_context_slots_size();

    // Global lifecycle flag to avoid use-after-destruction during interpreter/module teardown.
    static std::atomic<bool> shutting_down;

  public:
    TaintEngineContext();

    // Lifecycle control: mark the context as shutting down to prevent further access.
    static void set_shutting_down(bool v);

    // Fast-path: get the taint map for a known context_id (slot index).
    // Returns nullptr if the slot is empty or out of lifecycle.
    TaintedObjectMapTypePtr get_tainted_object_map_by_ctx_id(size_t ctx_id);

    // Slow-path: find and return the taint map that contains the given
    // tainted object across all active slots. Returns nullptr if not found or
    // object has no taint ranges.
    TaintedObjectMapTypePtr get_tainted_object_map(PyObject* tainted_object);

    // Single PyObject lookup: scans active maps to locate the tainted object
    // itself (no container traversal). This contains the original logic of
    // get_tainted_object_map prior to adding container-aware behavior.
    TaintedObjectMapTypePtr get_tainted_object_map_from_pyobject(PyObject* tainted_object);

    //    TaintedObjectMapTypePtr get_tainted_object_map_from_list_of_pyobjects(std::initializer_list<PyObject*>
    //    objects);

    TaintedObjectMapTypePtr get_tainted_object_map_from_list_of_pyobjects(const std::vector<PyObject*>& objects);

    // Given a collection of TaintRange shared pointers, scan all active
    // request context maps and return the first map that contains at least
    // one of these ranges in any of its stored TaintedObjects. Returns nullptr
    // if none of the ranges are found in any active map.
    // AIDEV-QUESTION: Should this require all ranges to be found in the same
    // map/object, or is finding any one sufficient? Implemented as "any"
    // for now for performance and broader matching.
    TaintedObjectMapTypePtr get_tainted_object_map_from_ranges(const TaintRangeRefs& ranges);

    // Clear a specific map if present; leaves the slot free for reuse.
    void finish_request_context(size_t ctx_id);

    // Clear all maps and free all slots.
    void clear_all_request_context_slots();

    // Convenience: retrieve the tainted object directly if present in any
    // active map; nullptr otherwise.
    TaintedObjectPtr get_tainted_object_from_request_context_slot(PyObject* tainted_object);

    // Create a context map for the current request. Returns the index (slot)
    // of the created map when successful. If capacity is saturated, the
    // returned optional may encode an invalid value (implementation detail),
    // and callers should treat that as failure to allocate a slot.
    std::optional<size_t> start_request_context();

    // Introspection helpers
    size_t debug_context_array_size() const { return request_context_slots.size(); }

    int debug_num_tainted_objects(size_t ctx_id);

    string debug_taint_map(size_t ctx_id);

    // Return the number of free slots (i.e., nullptr entries) in the
    // request_context_slots array. Intended for tests and diagnostics to
    // validate concurrency behavior under stress.
    size_t debug_context_array_free_slots_number() const
    {
        size_t free_count = 0;
        for (const auto& slot : request_context_slots) {
            if (slot == nullptr) {
                ++free_count;
            }
        }
        return free_count;
    }
};

extern std::unique_ptr<TaintEngineContext> taint_engine_context;

void
pyexport_taint_engine_context(py::module& m);
