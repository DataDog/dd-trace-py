//
// Created by alberto.vara on 22/05/23.
//

#ifndef _NATIVE_ASPECTOPERATORADD_H
#define _NATIVE_ASPECTOPERATORADD_H
#include <Python.h>
#include "../TaintedObject/TaintedObject.h"
#include "../TaintRange/TaintRange.h"
#include "../TaintedOps/TaintedOps.h"

PyObject* add_aspect(PyObject* self, PyObject* const* args, Py_ssize_t nargs);


#endif //_NATIVE_ASPECTOPERATORADD_H
