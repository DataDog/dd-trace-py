#pragma once
#include "Initializer/Initializer.h"
#include "TaintTracking/TaintRange.h"
#include "TaintTracking/TaintedObject.h"

PyObject*
api_add_aspect(PyObject* self, PyObject* const* args, Py_ssize_t nargs);
