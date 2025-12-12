#pragma once
#include <Python.h>

// Abiding by the contract required in the Python interface requires abandoning certain checks in one's main project.
// Rather than abandoning discipline, we can include this header in a way that bypasses these checks.
inline static PyCFunction
cast_to_pycfunction(PyObject* (*func)(PyObject*, PyObject*, PyObject*))
{
    // Direct C-style cast
    return (PyCFunction)(func);
}
