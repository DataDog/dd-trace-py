#pragma once

#include <Python.h>
#include <pybind11/pybind11.h>

using namespace std;
using namespace pybind11::literals;

namespace py = pybind11;

inline bool
is_dict_or_frozendict(PyObject* obj)
{
#if PY_VERSION_HEX >= 0x030F0000
    return PyAnyDict_Check(obj);
#else
    return PyDict_Check(obj);
#endif
}

bool
asbool(const py::object&);
bool
asbool(const char*);

void
iast_taint_log_error(const std::string&);

inline bool
is_iast_debug_enabled()
{
    const char* debug_value = std::getenv("_DD_IAST_DEBUG");
    if (debug_value == nullptr) {
        return false;
    }
    return asbool(debug_value);
}

inline py::object
get_python_logger();

inline bool
is_some_number(PyObject* obj)
{
    return PyLong_Check(obj) || PyFloat_Check(obj) || PyComplex_Check(obj);
}

py::object
safe_import(const char*, const char*);

bool
is_pointer_this_builtin(PyObject* orig_function, const char* builtin_name);
