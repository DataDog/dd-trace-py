#pragma once

#include <Python.h>
#include <pybind11/pybind11.h>
#include <iostream> // JJJ

using namespace std;
using namespace pybind11::literals;

namespace py = pybind11;
bool asbool(const py::object&);
bool
asbool(const char*);
void
iast_taint_log_error(const std::string&);

inline bool
is_iast_debug_enabled()
{
    cerr << "JJJ is_iast_debug_enabled 1\n";
    const char* debug_value = std::getenv("IAST_DEBUG");
    if (debug_value == nullptr) {  // JJJ
        cerr << "JJJ is_iast_debug_enabled 1.5, debug_value is nullptr" << endl;
    }
    cerr << "JJJ is_iast_debug_enabled 2, debug_value: " << debug_value << "\n";
    if (debug_value == nullptr) {
    cerr << "JJJ is_iast_debug_enabled 3, debug_value is nullptr" << endl;
        return false;
    }
    cerr << "JJJ is_iast_debug_enabled 4, debug_value: " << debug_value << "\n";
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

inline bool
is_some_number(PyObject* obj)
{
    return PyLong_Check(obj) || PyFloat_Check(obj) || PyComplex_Check(obj);
}
