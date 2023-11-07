#pragma once

#include <pybind11/pybind11.h>

using namespace std;
namespace py = pybind11;

inline string
str2lower(string_view s)
{
    string ret{ s };
    std::transform(ret.begin(), ret.end(), ret.begin(), [](unsigned char c) { return std::tolower(c); });
    return ret;
}

inline bool
str2bool(string_view s)
{
    string lowered = str2lower(s);
    return lowered == "yes" or lowered == "1" or lowered == "true";
}

inline bool
is_text(const PyObject* pyptr)
{
    if (!pyptr)
        return false;

    return PyUnicode_Check(pyptr) || PyBytes_Check(pyptr) || PyByteArray_Check(pyptr);
}

py::str
copy_string_new_id(const py::str& source);

py::bytes
copy_string_new_id(const py::bytes& source);

py::bytearray
copy_string_new_id(const py::bytearray& source);

py::object
copy_string_new_id(const py::object& source);

PyObject*
copy_string_new_str_id(PyObject* source);

void
pyexport_string_utils(py::module& m);
