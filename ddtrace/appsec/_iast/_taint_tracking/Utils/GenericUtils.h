#pragma once

#include <Python.h>
#include <pybind11/pybind11.h>

using namespace std;
using namespace pybind11::literals;

namespace py = pybind11;
bool asbool(py::object);
bool
asbool(const char*);
void
iast_taint_log_error(const std::string&);

inline bool
is_iast_debug_enabled()
{
    const char* debug_value = std::getenv("IAST_DEBUG");
    if (debug_value == nullptr) {
        return false;
    }
    return asbool(debug_value);
}

inline py::object
get_python_logger()
{
    py::object logger_module = py::module::import("ddtrace.internal.logger");
    py::object get_logger = logger_module.attr("get_logger");
    py::object native_logger = get_logger("native");
    return native_logger;
}
