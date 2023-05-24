#ifndef _TAINT_TRACKING_TAINTEDOBJECT_H
#define _TAINT_TRACKING_TAINTEDOBJECT_H
#include <Python.h>
#include <pybind11/pybind11.h>
#include "TaintTracking/TaintRange.h"
#include "TaintTracking/TaintedObject.h"

using namespace std;
namespace py = pybind11;

PyObject *setup(PyObject *Py_UNUSED(module), PyObject *args);

PyObject *new_pyobject_id(PyObject *tainted_object, Py_ssize_t object_length);

PyObject *api_new_pyobject_id(PyObject *Py_UNUSED(module), PyObject *args);

// TODO
//PyObject *api_add_taint_pyobject(PyObject* pyobject, PyObject* op1, PyObject* op2);
//PyObject* api_taint_pyobject(PyObject* pyobject, Source source);
//bool api_is_pyobject_tainted(PyObject* pyobject);
//void api_set_tainted_ranges(PyObject* pyobject, TaintRangeRefs ranges);
//TaintRangeRefs api_get_tainted_ranges(PyObject* pyobject); // can be already implemented
//XXX (Tuple[List[Dict[str, Union[Any, int]]], list[_Source]]) api_taint_ranges_as_evidence_info(PyObject* pyobject);


TaintedObject* get_tainted_object(const PyObject* str, TaintRangeMapType* tx_taint_map);

inline uintptr_t get_unique_id_pyo(const PyObject* pyo) {
    return uintptr_t(pyo);
}

inline static uintptr_t get_unique_id(const PyObject* pyo) {
    return uintptr_t(pyo);
}

inline static uintptr_t get_unique_id(const py::object& pyo) {
    return uintptr_t(pyo.ptr());
}

bool could_be_tainted(const PyObject* op);

void set_could_be_tainted(PyObject* op);
#endif //_TAINT_TRACKING_TAINTEDOBJECT_H