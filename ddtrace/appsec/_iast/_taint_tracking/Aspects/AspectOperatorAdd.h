#pragma once
#include "Initializer/Initializer.h"

PyObject*
api_add_aspect(PyObject* self, PyObject* const* args, Py_ssize_t nargs);

PyObject*
api_add_inplace_aspect(PyObject* self, PyObject* const* args, Py_ssize_t nargs);
