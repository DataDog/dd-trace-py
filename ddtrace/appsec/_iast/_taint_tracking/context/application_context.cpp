#include "application_context.h"
#include <cstdint>
#include <cstdlib>

using namespace std;

namespace {
static constexpr size_t DEFAULT_CAPACITY = 128;       // safe default per guidance
static constexpr size_t MIN_CAPACITY = 1;
static constexpr size_t MAX_CAPACITY = 65536;         // reasonable upper bound
}

size_t ApplicationContext::compute_capacity() {
    const char* env = std::getenv("DD_IAST_MAX_CONCURRENT_REQUESTS");
    if (!env || *env == '\0') {
        return DEFAULT_CAPACITY;
    }
    try {
        size_t v = static_cast<size_t>(std::stoull(env));
        if (v < MIN_CAPACITY) return MIN_CAPACITY;
        if (v > MAX_CAPACITY) return MAX_CAPACITY;
        return v;
    } catch (...) {
        return DEFAULT_CAPACITY;
    }
}

ApplicationContext::ApplicationContext()
    : contexts_array(compute_capacity(), nullptr), context_id(std::nullopt) {}

TaintRangeMapTypePtr ApplicationContext::create_context_map() {
    // If there is already an active context, return it (ensure non-null)
    if (context_id.has_value()) {
        auto& slot = contexts_array[*context_id];
        if (!slot) {
            slot = make_shared<TaintRangeMapType>();
        }
        return slot;
    }
    // Find first free slot
    for (size_t i = 0; i < contexts_array.size(); ++i) {
        if (contexts_array[i] == nullptr) {
            auto map_ptr = make_shared<TaintRangeMapType>();
            contexts_array[i] = map_ptr;
            context_id = i;
            return map_ptr;
        }
    }
    // Saturated
    return nullptr;
}

TaintRangeMapTypePtr ApplicationContext::get_contexts_array() {
    if (!context_id.has_value()) {
        return nullptr;
    }
    return contexts_array[*context_id];
}

void ApplicationContext::clear_tainting_map(const TaintRangeMapTypePtr &tx_map) {
    if (!tx_map) return;
    for (size_t i = 0; i < contexts_array.size(); ++i) {
        if (contexts_array[i] == tx_map) {
            if (contexts_array[i]) {
                contexts_array[i]->clear();
            }
            contexts_array[i] = nullptr;
            if (context_id.has_value() && *context_id == i) {
                context_id.reset();
            }
            break;
        }
    }
}

void ApplicationContext::clear_tainting_maps() {
    for (auto &slot : contexts_array) {
        if (slot) {
            slot->clear();
            slot = nullptr;
        }
    }
    context_id.reset();
}

void ApplicationContext::reset_context() {
    if (!context_id.has_value()) {
        return;
    }
    auto idx = *context_id;
    if (idx < contexts_array.size()) {
        contexts_array[idx] = nullptr;
    }
    context_id.reset();
}

size_t ApplicationContext::contexts_in_use() const {
    size_t n = 0;
    for (const auto &slot : contexts_array) {
        if (slot) ++n;
    }
    return n;
}

void pyexport_application_context(py::module &m) {
    m.def("clear_tainting_maps", [] { application_context->clear_tainting_maps(); });
    m.def("create_context_map", [] { return application_context->create_context_map() != nullptr; });
    m.def("get_contexts_array", [] { return application_context->get_contexts_array() != nullptr; });
    m.def("reset_context", [] { application_context->reset_context(); });
    m.def("is_request_enabled", [] { return application_context->is_request_enabled(); });
    m.def("capacity", [] { return application_context->capacity(); });
    m.def("contexts_in_use", [] { return application_context->contexts_in_use(); });
    // Return current context id (or None)
    m.def("get_context_id", []() -> py::object {
        auto cid = application_context->get_context_id();
        if (cid.has_value()) {
            return py::int_(*cid);
        }
        return py::none();
    });
    // Return the raw pointer value of the current context map slot (0 if none)
    m.def("get_context_slot_addr", []() -> unsigned long long {
        auto map_ptr = application_context->get_contexts_array();
        return map_ptr ? static_cast<unsigned long long>(reinterpret_cast<uintptr_t>(map_ptr.get())) : 0ULL;
    });

    // Benchmark helpers
    m.def("create_context_map_bench", [] { (void)application_context->create_context_map(); });
    m.def("get_context_map_bench", [] { (void) application_context->get_contexts_array(); });
}

std::unique_ptr<ApplicationContext> application_context;
