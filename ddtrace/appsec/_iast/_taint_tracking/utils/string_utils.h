#pragma once

#include <Python.h>
#include <pybind11/pybind11.h>

#include "generic_utils.h"

using namespace std;
using namespace pybind11::literals;

namespace py = pybind11;

#define GET_HASH_KEY(hash) (hash & 0xFFFFFF)

typedef struct _PyASCIIObject_State_Hidden
{
    unsigned int : 8;
    unsigned int hidden : 24;
} PyASCIIObject_State_Hidden;

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

bool
PyIOBase_Check(const PyObject* obj);

bool
PyReMatch_Check(const PyObject* obj);

bool
is_notinterned_notfasttainted_unicode(const PyObject* objptr);

void
set_fast_tainted_if_notinterned_unicode(PyObject* objptr);

inline bool
is_text(const PyObject* pyptr)
{
    if (pyptr == nullptr) {
        return false;
    };

    // Check that it's aligned correctly
    if (reinterpret_cast<uintptr_t>(pyptr) % alignof(PyObject) != 0)
        return false;
    ;

    // Try to safely access ob_type
    if (const PyObject* temp = pyptr; !temp->ob_type)
        return false;

    return PyUnicode_Check(pyptr) or PyBytes_Check(pyptr) or PyByteArray_Check(pyptr);
}

inline bool
is_tainteable(const PyObject* pyptr)
{
    return pyptr != nullptr and (is_text(pyptr) or PyReMatch_Check(pyptr) or PyIOBase_Check(pyptr));
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
    if (first == nullptr || second == nullptr) {
        return false;
    }

    const auto type_first = PyObject_Type(first);
    const auto type_second = PyObject_Type(second);

    // Check if both first and second are valid text types and of the same type
    if (!is_text(first) || !is_text(second) || type_first != type_second) {
        Py_XDECREF(type_first);
        Py_XDECREF(type_second);
        return false;
    }

    Py_XDECREF(type_first);
    Py_XDECREF(type_second);
    // Recursively check the rest of the arguments
    return args_are_text_and_same_type(second, args...);
}

PyTextType
get_pytext_type(PyObject* obj);

PyObject*
new_pyobject_id(PyObject* tainted_object);

size_t
get_pyobject_size(PyObject* obj);

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
            return py::none();
    }
}

inline py::object
StringToPyObject(const char* str, const PyTextType type)
{
    return StringToPyObject(string(str), type);
}

inline string
PyObjectToString(PyObject* obj)
{
    if (obj == nullptr or !PyUnicode_Check(obj)) {
        return "";
    }

    const char* str = PyUnicode_AsUTF8(obj);

    if (str == nullptr) {
        return "";
    }
    return str;
}

inline string
AnyTextObjectToString(const py::handle& py_string_like)
{
    // Ensure py_string_like is recognized as a pybind11 object
    auto obj = py::reinterpret_borrow<py::object>(py_string_like);

    if (py::isinstance<py::str>(obj)) {
        return obj.cast<string>();
    }
    if (py::isinstance<py::bytes>(obj)) {
        return obj.cast<string>();
    }
    if (py::isinstance<py::bytearray>(obj)) {
        return obj.cast<string>();
    }

    return {};
}

inline optional<py::object>
PyObjectToPyText(PyObject* obj)
{
    if (PyUnicode_Check(obj)) {
        return py::reinterpret_borrow<py::str>(obj);
    }
    if (PyBytes_Check(obj)) {
        return py::reinterpret_borrow<py::bytes>(obj);
    }
    if (PyByteArray_Check(obj)) {
        return py::reinterpret_borrow<py::bytearray>(obj);
    }

    // If it's not a recognized type, return an empty std::optional
    return std::nullopt;
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

inline std::string
slice_pystr_to_string(const py::str& text, long start, long end, long step = 1)
{
    // Create a Python slice [start:end:step]
    py::slice slice(start, end, step);

    // Apply the slice to the py::str and convert the result to std::string
    py::object sliced_text = text[slice];
    return py::cast<std::string>(sliced_text);
}

/*
 Disabled, enable in the future if we use libicu for unicode handling stuff
string
substrCodePoints(const string& str, const long start, const long length = -1)
{
    icu::UnicodeString uStr = icu::UnicodeString::fromUTF8(str);

    // Convert start and length from code point units to UTF-16 code units
    const long utf16Start = uStr.moveIndex32(0, start); // Move from 0 to the start code point index
    const long utf16Length = (length == -1) ? uStr.countChar32() - start : length;
    const long utf16End = uStr.moveIndex32(utf16Start, utf16Length); // Get the UTF-16 index at the end of the substring

    // Extract the substring in UTF-16 space and convert it back to UTF-8
    const icu::UnicodeString uSubStr = uStr.tempSubStringBetween(utf16Start, utf16End);

    string result;
    uSubStr.toUTF8String(result);
    return result;
}
*/
