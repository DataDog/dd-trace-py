#ifndef _NATIVE_ASPECTJOIN_H
#define _NATIVE_ASPECTJOIN_H
#include <Python.h>
#include "TaintedObject/TaintedObject.h"
#include "TaintRange/TaintRange.h"
#include "TaintedOps/TaintedOps.h"

PyObject* join_aspect(PyObject* self, PyObject* const* args, Py_ssize_t nargs);


#endif //_NATIVE_ASPECTJOIN_H
