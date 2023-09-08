// Note: some definitions are in TaintFuncs.h to avoid the problem of Python
// giving the "module not found in flat namespace" on import for templated
// functions.

// Needed for conversions from Vector to Tuple in get_ranges, dont remove even
// if CLion tells it's not used!
#include "StringUtils.h"

// TODO: check if really needed
// #define PY_SSIZE_T_CLEAN
#include <Python.h>

using namespace std;
using namespace pybind11::literals;

py::str
copy_string_new_id(const py::str& source)
{
    if (PyUnicode_CHECK_INTERNED(source.ptr()) == SSTATE_NOT_INTERNED) {
        return source;
    }

    py::str newstr = string(source);
    return newstr;
}

py::bytes
copy_string_new_id(const py::bytes& source)
{
    if (PyUnicode_CHECK_INTERNED(source.ptr()) == SSTATE_NOT_INTERNED) {
        return source;
    }
    auto newstr = strdup(PYBIND11_BYTES_AS_STRING(source.ptr()));
    py::bytes newbytes = newstr;
    free(newstr);
    return newbytes;
}

py::bytearray
copy_string_new_id(const py::bytearray& source)
{
    return source;
}

py::object
copy_string_new_id(const py::object& source)
{
    return source;
}

PyObject*
copy_string_new_str_id(PyObject* source)
{
    if (PyUnicode_CHECK_INTERNED(source) == SSTATE_NOT_INTERNED) {
        return source;
    }

    const Py_ssize_t length = PyUnicode_GET_LENGTH(source);
    if (length >= 4097) {
        return source;
    }
    const Py_ssize_t byte_length = length * PyUnicode_KIND(source);

    // Despite its name, this macro computes the a reference maxchar based on
    // is_ascii and kind. It does not iterate contents at all. Which other
    // variants like ucs1lib_find_max_char do.
    const Py_UCS4 maxchar = PyUnicode_MAX_CHAR_VALUE(source);
    // Using this PyUnicode_New constructor, and the manual copy, we avoid any
    // iteration of contents too. And we also avoid any decoding process like
    // unicode_decode_utf8.
    PyObject* newobj = PyUnicode_New(length, maxchar);
    memcpy(PyUnicode_DATA(newobj), PyUnicode_DATA(source), byte_length);
    Py_DECREF(source);
    return newobj;
}

void
pyexport_string_utils(py::module& m)
{
    m.def("copy_string_new_id",
          py::overload_cast<const py::bytes&>(&copy_string_new_id),
          "s"_a,
          py::return_value_policy::move);
    m.def("copy_string_new_id",
          py::overload_cast<const py::str&>(&copy_string_new_id),
          "s"_a,
          py::return_value_policy::move);
    m.def("copy_string_new_id",
          py::overload_cast<const py::bytearray&>(&copy_string_new_id),
          "s"_a,
          py::return_value_policy::move);
    m.def("copy_string_new_id",
          py::overload_cast<const py::object&>(&copy_string_new_id),
          "s"_a,
          py::return_value_policy::move);
}
