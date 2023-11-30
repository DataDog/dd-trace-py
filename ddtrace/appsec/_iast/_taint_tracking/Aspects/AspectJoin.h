#pragma once
#include "Aspects/Helpers.h"
#include "Initializer/Initializer.h"
#include "TaintTracking//TaintedObject.h"
#include "TaintTracking/TaintRange.h"
#include "TaintedOps/TaintedOps.h"

namespace py = pybind11;

PyObject*
api_join_aspect(PyObject* self, PyObject* const* args, Py_ssize_t nargs);
