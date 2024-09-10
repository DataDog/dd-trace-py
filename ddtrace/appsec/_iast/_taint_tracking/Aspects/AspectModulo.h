#pragma once
#include "Initializer/Initializer.h"

namespace py = pybind11;

PyObject*
api_modulo_aspect(PyObject* self, PyObject* const* args, const Py_ssize_t nargs);
