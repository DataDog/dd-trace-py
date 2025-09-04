// ApplicationContext implementation
//
// This component manages an array of taint maps (TaintRangeMapTypePtr), one per
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
// - create_context_array(): allocates a fresh taint map into the first free
//   slot and returns its index.
// - clear_taint_map(id): clears a specific slot and frees it for reuse.
// - clear_contexts_array(): clears all slots.
//
// Pybind exports (see pyexport_application_context below): minimal helpers for
// diagnostics and micro-benchmarks (slot address, capacity, presence checks).
//
#include "application_context.h"
#include "taint_tracking/tainted_object.h"
#include <cstdint>
#include <cstdlib>

using namespace std;

namespace {
static constexpr size_t DEFAULT_CAPACITY = 2; // safe default per guidance
static constexpr size_t MIN_CAPACITY = 1;
static constexpr size_t MAX_CAPACITY = 1024; // reasonable upper bound
}

size_t
ApplicationContext::compute_capacity()
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

ApplicationContext::ApplicationContext()
  : contexts_array(compute_capacity(), nullptr)
{
}

std::optional<size_t>
ApplicationContext::create_context_array()
{
    for (size_t i = 0; i < contexts_array.size(); ++i) {
        if (contexts_array[i] == nullptr) {
            auto map_ptr = make_shared<TaintRangeMapType>();
            contexts_array[i] = map_ptr;
            return i;
        }
    }
    // Saturated
    return -1;
}

TaintRangeMapTypePtr
ApplicationContext::get_taint_map_by_ctx_id(size_t ctx_id)
{
    return contexts_array[ctx_id];
}

TaintRangeMapTypePtr
ApplicationContext::get_taint_map(PyObject* tainted_object)
{
    for (const auto& context_map : contexts_array) {
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
ApplicationContext::get_tainted_object_from_contexts_array(PyObject* tainted_object)
{
    for (const auto& context_map : contexts_array) {
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

void
ApplicationContext::clear_taint_map(size_t ctx_id)
{
    if (contexts_array[ctx_id]) {
        contexts_array[ctx_id]->clear();
    }
    contexts_array[ctx_id] = nullptr;
}

void
ApplicationContext::clear_contexts_array()
{
    for (auto& slot : contexts_array) {
        if (slot) {
            slot->clear();
            slot = nullptr;
        }
    }
}

void
pyexport_application_context(py::module& m)
{
    m.def("clear_taint_map", [](size_t ctx_id) { application_context->clear_taint_map(ctx_id); });
    m.def("create_context_array", [] { return application_context->create_context_array(); });
    m.def("clear_contexts_array", [] { return application_context->clear_contexts_array(); });
    m.def("get_taint_map_by_ctx_id",
          [](size_t ctx_id) { return application_context->get_taint_map_by_ctx_id(ctx_id) != nullptr; });
    m.def("get_taint_map", [](py::object tainted_obj) {
        auto map_ptr = application_context->get_taint_map(tainted_obj.ptr());
        return map_ptr != nullptr;
    });
    m.def("capacity", [] { return application_context->capacity(); });
    // Return the raw pointer value of the given context map slot (0 if none)
    m.def("get_context_slot_addr", [](size_t ctx_id) -> unsigned long long {
        auto map_ptr = application_context->get_taint_map_by_ctx_id(ctx_id);
        return map_ptr ? static_cast<unsigned long long>(reinterpret_cast<uintptr_t>(map_ptr.get())) : 0ULL;
    });
}

std::unique_ptr<ApplicationContext> application_context;
