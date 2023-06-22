#pragma once
#include "Initializer/Initializer.h"
#include "TaintTracking/TaintRange.h"
#include "TaintTracking/TaintedObject.h"
#include <Python.h>
#include <pybind11/pybind11.h>

using namespace std;
using namespace pybind11::literals;
namespace py = pybind11;

PyObject*
setup(PyObject* Py_UNUSED(module), PyObject* args);

PyObject*
new_pyobject_id(PyObject* tainted_object, Py_ssize_t object_length);

PyObject*
api_new_pyobject_id(PyObject* Py_UNUSED(module), PyObject* args);

bool
is_tainted(PyObject* Py_UNUSED(module), PyObject* args);

bool
api_is_tainted(py::object tainted_object);

void
pyexport_tainted_ops(py::module& m);
// TODO
// PyObject *api_add_taint_pyobject(PyObject* pyobject, PyObject* op1, PyObject*
// op2); PyObject* api_taint_pyobject(PyObject* pyobject, Source source); bool
// api_is_pyobject_tainted(PyObject* pyobject); void
// api_set_tainted_ranges(PyObject* pyobject, TaintRangeRefs ranges);
// TaintRangeRefs api_get_tainted_ranges(PyObject* pyobject); // can be already
// implemented
// XXX (Tuple[List[Dict[str, Union[Any, int]]], list[Source]])
// api_taint_ranges_as_evidence_info(PyObject* pyobject);