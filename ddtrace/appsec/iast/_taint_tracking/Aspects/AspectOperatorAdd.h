#ifndef _NATIVE_ASPECTOPERATORADD_H
#define _NATIVE_ASPECTOPERATORADD_H
#include "Aspects/Helpers.h"
#include "Initializer/Initializer.h"
#include "TaintTracking/TaintRange.h"
#include "TaintTracking/TaintedObject.h"
#include "TaintedOps/TaintedOps.h"
#include <pybind11/pybind11.h>

PyObject*
api_add_aspect(PyObject* self, PyObject* const* args, Py_ssize_t nargs);

#endif //_NATIVE_ASPECTOPERATORADD_H
