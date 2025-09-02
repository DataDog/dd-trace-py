#pragma once
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <memory>
#include <optional>
#include <string>
#include <vector>

#include "taint_tracking/taint_range.h"

namespace py = pybind11;

class ApplicationContext {
  private:
    // Fixed capacity array of context maps
    std::vector<TaintRangeMapTypePtr> contexts_array;
    // Active context index if any
    std::optional<size_t> context_id;
    // Parse and clamp capacity from environment
    static size_t compute_capacity();

  public:
    ApplicationContext();

    // Create (or return) a context map for the current request. Returns nullptr if capacity saturated.
    TaintRangeMapTypePtr create_context_map();

    // Get current context map (fast path). Returns nullptr if no active context.
    TaintRangeMapTypePtr get_contexts_array();

    // Clear a specific map if present; leaves slot free.
    void clear_tainting_map(const TaintRangeMapTypePtr &tx_map);

    // Clear all maps and free all slots.
    void clear_tainting_maps();

    // Reset current context: free the active slot and unset context_id.
    void reset_context();

    // Whether a request context is currently active.
    bool is_request_enabled() const { return context_id.has_value(); }


    std::optional<size_t> get_context_id() const { return context_id; }

    // Introspection helpers
    size_t capacity() const { return contexts_array.size(); }
    size_t contexts_in_use() const;
};

extern std::unique_ptr<ApplicationContext> application_context;

void pyexport_application_context(py::module &m);
