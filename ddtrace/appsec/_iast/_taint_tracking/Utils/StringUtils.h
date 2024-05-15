#pragma once

#include <Python.h>
#include <pybind11/pybind11.h>
#include <iostream>  // JJJ

using namespace std;
using namespace pybind11::literals;

namespace py = pybind11;

inline static uintptr_t
get_unique_id(const PyObject* str)
{
    return uintptr_t(str);
}

bool
is_notinterned_notfasttainted_unicode(const PyObject* objptr);

void
set_fast_tainted_if_notinterned_unicode(PyObject* objptr);

inline bool
is_text(const PyObject* pyptr)
{
    std::cerr << "JJJ is_text start\n";
    if (!pyptr)
    {
        std::cerr << "JJJ return false no pointer\n";
        return false;
    }

    std::cerr << "JJJ is_text calling checks\n";
    std::cerr << "JJJ PyUnicode_Check\n";
    auto bool1 = PyUnicode_Check(pyptr);
    std::cerr << "JJJ PyBytes_Check\n";
    auto bool2 = PyBytes_Check(pyptr);
    std::cerr << "JJJ PyByteArray_Check\n";
    auto bool3 = PyByteArray_Check(pyptr);
    return bool1 || bool2 || bool3;
    // return PyUnicode_Check(pyptr) || PyBytes_Check(pyptr) || PyByteArray_Check(pyptr);
}

string
PyObjectToString(PyObject* obj);

PyObject*
new_pyobject_id(PyObject* tainted_object);

size_t
get_pyobject_size(PyObject* obj);