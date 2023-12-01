#pragma once
#include "Initializer/Initializer.h"
#include "TaintTracking/TaintRange.h"
#include "TaintTracking/TaintedObject.h"

namespace py = pybind11;

PyObject*
api_join_aspect(PyObject* self, PyObject* const* args, Py_ssize_t nargs);
