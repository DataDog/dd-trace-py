#ifndef _TAINT_TRACKING_TAINTEDOBJECT_H
#define _TAINT_TRACKING_TAINTEDOBJECT_H
#include <Python.h>
#include "../TaintedObject/TaintedObject.h"
#include "../TaintRange/TaintRange.h"

PyObject *setup(PyObject *Py_UNUSED(module), PyObject *args);

PyObject *new_pyobject_id(PyObject *tainted_object, Py_ssize_t object_length);

PyObject *api_new_pyobject_id(PyObject *Py_UNUSED(module), PyObject *args);

TaintedObject* get_tainted_object(const PyObject* str, TaintRangeMapType* tx_taint_map);

inline uintptr_t get_unique_id_pyo(const PyObject* pyo) {
    return uintptr_t(pyo);
}

inline static uintptr_t get_unique_id(const PyObject* pyo) {
    return uintptr_t(pyo);
}

bool could_be_tainted(const PyObject* op);

void set_could_be_tainted(PyObject* op);
#endif //_TAINT_TRACKING_TAINTEDOBJECT_H