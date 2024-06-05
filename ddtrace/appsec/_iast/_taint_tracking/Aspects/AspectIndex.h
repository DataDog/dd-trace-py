#pragma once
#include "TaintedOps/TaintedOps.h"

PyObject*
index_aspect(PyObject* result_o, PyObject* candidate_text, PyObject* idx, const TaintRangeMapTypePtr& tx_taint_map);
PyObject*
api_index_aspect(PyObject* self, PyObject* const* args, Py_ssize_t nargs);
