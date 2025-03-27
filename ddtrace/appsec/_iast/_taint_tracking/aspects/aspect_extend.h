#pragma once

#include "initializer/initializer.h"
#include <Python.h>

PyObject*
api_extend_aspect(PyObject* self, PyObject* const* args, Py_ssize_t nargs);
