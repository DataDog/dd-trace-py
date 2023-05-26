#pragma once

#include <pybind11/pybind11.h>

using namespace std;
namespace py = pybind11;

inline string str2lower(string_view s) {
    string ret{s};
    std::transform(ret.begin(), ret.end(), ret.begin(), [](unsigned char c) { return std::tolower(c); });
    return ret;
}

inline bool str2bool(string_view s) {
    string lowered = str2lower(s);
    return lowered == "yes" or lowered == "1" or lowered == "true";
}

// TODO: find a faster way directly with conversion with PyObject*
inline bool is_text(PyObject* pyptr) {
    // TODO: this makes a copy
    auto element = py::reinterpret_borrow<py::object>(pyptr);
    return py::isinstance<py::str>(element) || py::isinstance<py::bytes>(element) ||
           py::isinstance<py::bytearray>(element);
}

py::str copy_string_new_id(const py::str& source);

py::bytes copy_string_new_id(const py::bytes& source);

py::bytearray copy_string_new_id(const py::bytearray& source);

py::object copy_string_new_id(const py::object& source);

PyObject* copy_string_new_str_id(PyObject* source);

void pyexport_string_utils(py::module& m);
