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

string
PyObjectToString(PyObject* obj)
{
    const char* str = PyUnicode_AsUTF8(obj);

    if (str == nullptr) {
        PyErr_Print();
        throw runtime_error("PyObjectToString error");
    }
    return str;
}

PyObject*
new_pyobject_id(PyObject* tainted_object)
{
    if (PyUnicode_Check(tainted_object)) {
        PyObject* empty_unicode = PyUnicode_New(0, 127);
        PyObject* val = Py_BuildValue("(OO)", tainted_object, empty_unicode);
        PyObject* result = PyUnicode_Join(empty_unicode, val);
        Py_DecRef(empty_unicode);
        Py_DecRef(val);
        return result;
    }
    if (PyBytes_Check(tainted_object)) {
        PyObject* empty_bytes = PyBytes_FromString("");
        auto bytes_join_ptr = py::reinterpret_borrow<py::bytes>(empty_bytes).attr("join");
        auto val = Py_BuildValue("(OO)", tainted_object, empty_bytes);
        auto res = PyObject_CallFunctionObjArgs(bytes_join_ptr.ptr(), val, NULL);
        Py_DecRef(val);
        Py_DecRef(empty_bytes);
        return res;
    } else if (PyByteArray_Check(tainted_object)) {
        PyObject* empty_bytes = PyBytes_FromString("");
        PyObject* empty_bytearray = PyByteArray_FromObject(empty_bytes);
        auto bytearray_join_ptr = py::reinterpret_borrow<py::bytes>(empty_bytearray).attr("join");
        auto val = Py_BuildValue("(OO)", tainted_object, empty_bytearray);
        auto res = PyObject_CallFunctionObjArgs(bytearray_join_ptr.ptr(), val, NULL);
        Py_DecRef(val);
        Py_DecRef(empty_bytes);
        Py_DecRef(empty_bytearray);
        return res;
    }
    return tainted_object;
}