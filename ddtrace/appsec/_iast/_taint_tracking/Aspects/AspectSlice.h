#pragma once
#include "Aspects/Helpers.h"
#include "TaintedOps/TaintedOps.h"

PyObject*
api_slice_aspect(PyObject* self, PyObject* const* args, Py_ssize_t nargs);
