#ifndef _NATIVE_ASPECTOPERATORADD_H
#define _NATIVE_ASPECTOPERATORADD_H
#include <pybind11/pybind11.h>
#include "Initializer/Initializer.h"
#include "TaintTracking/TaintedObject.h"
#include "TaintTracking/TaintRange.h"
#include "TaintedOps/TaintedOps.h"

PyObject* api_add_aspect(PyObject* self, PyObject* const* args, Py_ssize_t nargs);


#endif //_NATIVE_ASPECTOPERATORADD_H
