// TaintEngineContext: request-identified array of taint maps
//
// Design overview
// - IAST_CONTEXT holds an opaque integer identifying one request. Its low bits
//   select a storage slot; its generation prevents copied execution contexts
//   from accessing another request after that slot is reused.
// - Map lookups return owning shared_ptr copies. Retirement detaches a map;
//   calls already using it finish against that map, never its replacement.
// - All access requires the GIL. Copy a map before calling Python, which can
//   reenter IAST and retire its slot even while the current call is running.
// - The array capacity is bounded and configurable via env var
//   DD_IAST_MAX_CONCURRENT_REQUESTS, and clamped to a safe range.
//
// Key operations
// - start_request_context(): returns a fresh request identifier, installing a
//   taint map in a free slot. Callers typically store this identifier in
//   IAST_CONTEXT at request start.
// - get_tainted_object_map_by_ctx_id(id): fast-path retrieval for the map in a given
//   slot; returns nullptr if the request has ended.
// - get_tainted_object_map(PyObject*): scans active maps to locate the one containing
//   the tainted object when no context_id is known (slow path / interop).
// - finish_request_context(id): retires that request and frees its slot.
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
#include <cstdint>
#include <memory>
#include <optional>
#include <string>
#include <vector>

#include "taint_tracking/taint_range.h"

namespace py = pybind11;

class TaintEngineContext
{
  private:
    static constexpr size_t SLOT_BITS = 10;
    static constexpr size_t SLOT_MASK = (size_t{ 1 } << SLOT_BITS) - 1;
    struct RequestContextSlot
    {
        std::uint64_t id = 0;
        TaintedObjectMapTypePtr map;
    };
    std::vector<RequestContextSlot> request_context_slots;
    // Parse and clamp capacity from environment
    static size_t assign_request_context_slots_size();

    // Global lifecycle flag to avoid use-after-destruction during interpreter/module teardown.
    static std::atomic<bool> shutting_down;

  public:
    TaintEngineContext();

    // Lifecycle control: mark the context as shutting down to prevent further access.
    static void set_shutting_down(bool v);
    static bool is_shutting_down() { return shutting_down.load(std::memory_order_acquire); }

    // Fast-path: validate a request identifier and acquire its map.
    // Returns nullptr for expired identifiers or outside the native lifecycle.
    TaintedObjectMapTypePtr get_tainted_object_map_by_ctx_id(std::uint64_t ctx_id);

    // Slow-path: find and return the taint map that contains the given
    // tainted object across all active slots. Returns nullptr if not found or
    // object has no taint ranges.
    TaintedObjectMapTypePtr get_tainted_object_map(PyObject* tainted_object);

    // Single PyObject lookup: scans active maps to locate the tainted object
    // itself (no container traversal). This contains the original logic of
    // get_tainted_object_map prior to adding container-aware behavior.
    TaintedObjectMapTypePtr get_tainted_object_map_from_pyobject(PyObject* tainted_object);

    // Container-aware membership check scoped to a single map. Mirrors the
    // list/tuple/dict traversal of get_tainted_object_map, but only consults
    // the provided map_ptr instead of scanning all active slots.
    static bool is_object_tainted_in_map(PyObject* obj, const TaintedObjectMapTypePtr& map_ptr);

    //    TaintedObjectMapTypePtr get_tainted_object_map_from_list_of_pyobjects(std::initializer_list<PyObject*>
    //    objects);

    TaintedObjectMapTypePtr get_tainted_object_map_from_list_of_pyobjects(const std::vector<PyObject*>& objects);

    // Given a collection of TaintRange shared pointers, scan all active
    // request context maps and return the first map that contains at least
    // one of these ranges in any of its stored TaintedObjects. Returns nullptr
    // if none of the ranges are found in any active map.
    // TODO: Should this require all ranges to be found in the same
    // map/object, or is finding any one sufficient? Implemented as "any"
    // for now for performance and broader matching.
    TaintedObjectMapTypePtr get_tainted_object_map_from_ranges(const TaintRangeRefs& ranges);

    // Detach a matching map; in-flight calls retain it until their last reference ends.
    void finish_request_context(std::uint64_t ctx_id);

    // Clear all maps and free all slots.
    void clear_all_request_context_slots();

    // Fork-safe: Abandon all slots without cleanup (for post-fork child process).
    // This skips calling destructors/cleanup on slots containing invalid PyObject pointers.
    void clear_tainted_object_map();

    // Convenience: retrieve the tainted object directly if present in any
    // active map; nullptr otherwise.
    TaintedObjectPtr get_tainted_object_from_request_context_slot(PyObject* tainted_object);

    // Create a context map and return its opaque request identifier, or
    // std::nullopt if capacity or identifier space is exhausted.
    std::optional<std::uint64_t> start_request_context();

    // Introspection helpers
    size_t debug_context_array_size() const { return request_context_slots.size(); }

    int debug_num_tainted_objects(std::uint64_t ctx_id);

    string debug_taint_map(std::uint64_t ctx_id);

    // Return the number of free slots (those with no attached map) in the
    // request_context_slots array. Intended for tests and diagnostics to
    // validate concurrency behavior under stress.
    size_t debug_context_array_free_slots_number() const;
};

extern std::unique_ptr<TaintEngineContext> taint_engine_context;

void
pyexport_taint_engine_context(py::module& m);
