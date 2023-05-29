#pragma once
#include <Python.h>
#include "Initializer/Initializer.h"
#include "TaintTracking//TaintedObject.h"
#include "TaintTracking/TaintRange.h"
#include "TaintedOps/TaintedOps.h"

PyObject* api_join_aspect(PyObject* self, PyObject* const* args, Py_ssize_t nargs);
