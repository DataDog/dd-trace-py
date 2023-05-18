#ifndef _TAINT_TRACKING_TAINTEDOBJECT_H
#define _TAINT_TRACKING_TAINTEDOBJECT_H
#include <Python.h>
#include <iostream>
#include <tuple>

PyObject *setup(PyObject *Py_UNUSED(module), PyObject *args);

PyObject *new_pyobject_id(PyObject *Py_UNUSED(module), PyObject *args);
#endif //_TAINT_TRACKING_TAINTEDOBJECT_H
