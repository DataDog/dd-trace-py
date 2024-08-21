#pragma once

#include <Python.h>
#include <pybind11/pybind11.h>

using namespace std;
using namespace pybind11::literals;

namespace py = pybind11;

inline static uintptr_t
get_unique_id(const PyObject* str)
{
    return reinterpret_cast<uintptr_t>(str);
}

inline static bool PyReMatch_Check(const PyObject* obj)
{
    PyObject* re_module = PyImport_ImportModule("re");
    PyTypeObject* match_type = (PyTypeObject*)PyObject_GetAttrString(re_module, "Match");
    bool res = PyObject_IsInstance(obj, match_type);
    // Alternatively:
    // bool res = PyType_IsSubtype(Py_TYPE(obj), match_type);
    Py_DECREF(re_module);
    Py_DECREF(match_type);
    return res;
}

bool
is_notinterned_notfasttainted_unicode(const PyObject* objptr);

void
set_fast_tainted_if_notinterned_unicode(PyObject* objptr);

inline bool
is_text(const PyObject* pyptr)
{
    if (!pyptr)
        return false;

    return PyUnicode_Check(pyptr) || PyBytes_Check(pyptr) || PyByteArray_Check(pyptr) || PyReMatch_Check(pyptr);
}

string
PyObjectToString(PyObject* obj);

PyObject*
new_pyobject_id(PyObject* tainted_object);

size_t
get_pyobject_size(PyObject* obj);
