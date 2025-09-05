// TaintEngineContext implementation
//
// This component manages an array of taint maps (TaintedObjectMapTypePtr), one per
// concurrent request/task. Python stores the active slot index in a ContextVar
// (IAST_CONTEXT). Fast-path operations use that index to retrieve the map.
// When only a PyObject is available (no context id), we provide a slow-path
// scan across active maps to find the matching tainted object.
//
// Capacity:
// - The number of slots is determined at construction time by reading the
//   environment variable DD_IAST_MAX_CONCURRENT_REQUESTS. The value is clamped
//   to [MIN_CAPACITY, MAX_CAPACITY], with a DEFAULT_CAPACITY fallback.
//
// Lifecycle:
// - start_request_context(): allocates a fresh taint map into the first free
//   slot and returns its index.
// - finish_request_context(id): clears a specific slot and frees it for reuse.
// - clear_all_request_context_slots(): clears all slots.
//
// Pybind exports (see pyexport_taint_engine_context below): minimal helpers for
// diagnostics and micro-benchmarks (slot address, capacity, presence checks).
//
#include "taint_engine_context.h"
#include "taint_tracking/tainted_object.h"
#include <cstdint>
#include <cstdlib>
#include <initializer_list>

using namespace std;

namespace {
static constexpr size_t DEFAULT_CAPACITY = 2; // safe default per guidance
static constexpr size_t MIN_CAPACITY = 1;
static constexpr size_t MAX_CAPACITY = 1024; // reasonable upper bound
}

// AIDEV-NOTE: Return the first taint map found when scanning a list of candidate PyObjects.
// The map must exist and be non-empty to be considered valid.
TaintedObjectMapTypePtr
TaintEngineContext::get_tainted_object_map_from_list_of_pyobjects(std::initializer_list<PyObject*> objects)
{
    for (auto* obj : objects) {
        if (!obj) {
            continue;
        }
        auto map = get_tainted_object_map(obj);
        if (map && !map->empty()) {
            return map;
        }
    }
    return nullptr;
}

TaintedObjectMapTypePtr
TaintEngineContext::get_tainted_object_map_from_list_of_pyobjects(const std::vector<PyObject*>& objects)
{
    for (auto* obj : objects) {
        if (!obj) {
            continue;
        }
        auto map = get_tainted_object_map(obj);
        if (map && !map->empty()) {
            return map;
        }
    }
    return nullptr;
}

size_t
TaintEngineContext::assign_request_context_slots_size()
{
    const char* env = std::getenv("DD_IAST_MAX_CONCURRENT_REQUESTS");
    if (!env || *env == '\0') {
        return DEFAULT_CAPACITY;
    }
    try {
        size_t v = static_cast<size_t>(std::stoull(env));
        if (v < MIN_CAPACITY)
            return MIN_CAPACITY;
        if (v > MAX_CAPACITY)
            return MAX_CAPACITY;
        return v;
    } catch (...) {
        return DEFAULT_CAPACITY;
    }
}

TaintEngineContext::TaintEngineContext()
  : request_context_slots(assign_request_context_slots_size(), nullptr)
{
}

std::optional<size_t>
TaintEngineContext::start_request_context()
{
    for (size_t i = 0; i < request_context_slots.size(); ++i) {
        if (request_context_slots[i] == nullptr) {
            auto map_ptr = make_shared<TaintedObjectMapType>();
            request_context_slots[i] = map_ptr;
            return i;
        }
    }
    // Saturated
    return -1;
}

void
TaintEngineContext::finish_request_context(size_t ctx_id)
{
    if (request_context_slots[ctx_id]) {
        request_context_slots[ctx_id]->clear();
    }
    request_context_slots[ctx_id] = nullptr;
}

void
TaintEngineContext::clear_all_request_context_slots()
{
    for (auto& slot : request_context_slots) {
        if (slot) {
            slot->clear();
            slot = nullptr;
        }
    }
}

TaintedObjectMapTypePtr
TaintEngineContext::get_tainted_object_map(PyObject* tainted_object)
{
    for (const auto& context_map : request_context_slots) {
        if (!context_map) {
            continue;
        }
        const auto& to_initial = get_tainted_object(tainted_object, context_map);
        if (to_initial && !to_initial->get_ranges().empty()) {
            return context_map;
        }
    }
    return nullptr;
}

TaintedObjectPtr
TaintEngineContext::get_tainted_object_from_request_context_slot(PyObject* tainted_object)
{
    for (const auto& context_map : request_context_slots) {
        if (!context_map) {
            continue;
        }
        const auto& to_initial = get_tainted_object(tainted_object, context_map);
        if (to_initial && !to_initial->get_ranges().empty()) {
            return to_initial;
        }
    }
    return nullptr;
}

string
TaintEngineContext::debug_taint_map(size_t ctx_id)
{
    const auto ctx_map = get_tainted_object_map_by_ctx_id(ctx_id);
    if (!ctx_map) {
        return ("[]");
    }

    std::stringstream output;
    output << "[";
    for (const auto& [fst, snd] : *ctx_map) {
        output << "{ 'Id-Key': " << fst << ",";
        output << "'Value': { 'Hash': " << snd.first << ", 'TaintedObject': '" << snd.second->toString() << "'}},";
    }
    output << "]";
    return output.str();
}

int
TaintEngineContext::debug_num_tainted_objects(size_t ctx_id)
{
    if (const auto ctx_map = get_tainted_object_map_by_ctx_id(ctx_id)) {
        return static_cast<int>(ctx_map->size());
    }
    return 0;
}

void
pyexport_taint_engine_context(py::module& m)
{
    m.def("finish_request_context", [](size_t ctx_id) { taint_engine_context->finish_request_context(ctx_id); });
    m.def("start_request_context", [] { return taint_engine_context->start_request_context(); });
    m.def("clear_all_request_context_slots", [] { return taint_engine_context->clear_all_request_context_slots(); });
    m.def("get_tainted_object_map_by_ctx_id",
          [](size_t ctx_id) { return taint_engine_context->get_tainted_object_map_by_ctx_id(ctx_id) != nullptr; });
    m.def("is_in_taint_map", [](py::object tainted_obj) {
        auto map_ptr = taint_engine_context->get_tainted_object_map(tainted_obj.ptr());
        return map_ptr != nullptr;
    });
    m.def("debug_context_array_size", [] { return taint_engine_context->debug_context_array_size(); });

    m.def("debug_taint_map", [](size_t ctx_id) { return taint_engine_context->debug_taint_map(ctx_id); });

    m.def("debug_debug_num_tainted_objects",
          [](size_t ctx_id) { return taint_engine_context->debug_num_tainted_objects(ctx_id); });
}

std::unique_ptr<TaintEngineContext> taint_engine_context;
