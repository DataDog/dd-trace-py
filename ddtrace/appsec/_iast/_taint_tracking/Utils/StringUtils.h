#pragma once

#include <Python.h>
#include <pybind11/pybind11.h>

#include "GenericUtils.h"

using namespace std;
using namespace pybind11::literals;

namespace py = pybind11;

enum class PyTextType
{
    UNICODE = 0,
    BYTES,
    BYTEARRAY,
    OTHER,
};

inline uintptr_t
get_unique_id(const PyObject* str)
{
    return reinterpret_cast<uintptr_t>(str);
}

static bool
PyReMatch_Check(const PyObject* obj)
{
    return py::isinstance((PyObject*)obj, py::module_::import("re").attr("Match"));
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

    return PyUnicode_Check(pyptr) || PyBytes_Check(pyptr) || PyByteArray_Check(pyptr);
}

inline bool
is_tainteable(const PyObject* pyptr)
{
    return is_text(pyptr) || PyReMatch_Check(pyptr);
}

// Base function for the variadic template
inline bool
args_are_text_and_same_type(PyObject* first)
{
    return (first != nullptr) and is_text(first);
}

// Recursive case for the argument checking variadic template
template<typename... Args>
bool
args_are_text_and_same_type(PyObject* first, PyObject* second, Args... args)
{
    // Check if both first and second are valid text types and of the same type
    if (first == nullptr || second == nullptr || !is_text(first) || !is_text(second) ||
        PyObject_Type(first) != PyObject_Type(second)) {
        return false;
    }

    // Recursively check the rest of the arguments
    return args_are_text_and_same_type(second, args...);
}

string
PyObjectToString(PyObject* obj);

PyTextType
get_pytext_type(PyObject* obj);

PyObject*
new_pyobject_id(PyObject* tainted_object);

size_t
get_pyobject_size(PyObject* obj);

string
AnyTextPyObjectToString(const py::handle& py_string_like);

string
AnyTextPyObjectToString(PyObject* py_string_like);

inline py::object
StringToPyObject(const string& str, const PyTextType type)
{
    switch (type) {
        case PyTextType::UNICODE:
            return py::str(str);
        case PyTextType::BYTES:
            return py::bytes(str);
        case PyTextType::BYTEARRAY:
            return py::bytearray(str);
        default:
            return {};
    }
}

inline py::object
StringToPyObject(const char* str, const PyTextType type)
{
    return StringToPyObject(string(str), type);
}

inline PyTextType
get_pytext_type(PyObject* obj)
{
    if (PyUnicode_Check(obj)) {
        return PyTextType::UNICODE;
    }
    if (PyBytes_Check(obj)) {
        return PyTextType::BYTES;
    }
    if (PyByteArray_Check(obj)) {
        return PyTextType::BYTEARRAY;
    }
    return PyTextType::OTHER;
}