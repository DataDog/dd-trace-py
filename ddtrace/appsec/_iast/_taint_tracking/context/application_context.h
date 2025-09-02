#pragma once
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <memory>
#include <queue>
#include <shared_mutex>
#include <string>
#include <unordered_map>

#include "taint_tracking/taint_range.h"
#include "context/request_context.h"

namespace py = pybind11;

class ApplicationContext {
  private:
    static constexpr size_t MAX_SIZE = 4000;
    std::unordered_map<std::string, TaintRangeMapTypePtr> context_maps;
    std::queue<std::string> context_order;
    mutable std::shared_mutex context_mutex; // reader-optimized

    static std::string to_string_or_empty(const py::object &obj) {
        try {
            if (obj.is_none()) {
                return std::string();
            }
            py::str s = py::str(obj);
            return std::string(py::cast<std::string>(s));
        } catch (...) {
            return std::string();
        }
    }

    void enforce_max_size();

  public:
    ApplicationContext() = default;

    // Retrieve current Python ContextVar IAST_CONTEXT as a string identifier (minimal allocs)
    std::string get_context_id() const;

    // Create and register a context map for current context id
    TaintRangeMapTypePtr create_context_map();

    // Get context map for provided context id (preferred fast path)
    TaintRangeMapTypePtr get_context_map(const std::string &ctx_id);

    // Convenience: fetch current id and delegate to parameterized version
    TaintRangeMapTypePtr get_context_map();

    // Clear a specific map (if it's tracked)
    void clear_tainting_map(const TaintRangeMapTypePtr &tx_map);

    // Clear all maps
    void clear_tainting_maps();

    // Ensure a context exists; create if missing. Returns the context id.
    std::string create_context();

    // Reset logic per specification
    void reset_context();

    // Introspection helpers
    size_t context_maps_size() const { return context_maps.size(); }
};

extern std::unique_ptr<ApplicationContext> application_context;

void pyexport_application_context(py::module &m);
