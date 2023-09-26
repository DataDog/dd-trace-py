#pragma once
#include "Aspects/Helpers.h"
#include "Initializer/Initializer.h"
#include "TaintTracking/TaintRange.h"
#include "TaintTracking/TaintedObject.h"
#include "TaintedOps/TaintedOps.h"
#include <pybind11/pybind11.h>

using namespace pybind11::literals;
namespace py = pybind11;

PyObject*
index_aspect(PyObject* result_o, PyObject* candidate_text, PyObject* idx, TaintRangeMapType* tx_taint_map);
PyObject*
api_index_aspect(PyObject* self, PyObject* const* args, Py_ssize_t nargs);
