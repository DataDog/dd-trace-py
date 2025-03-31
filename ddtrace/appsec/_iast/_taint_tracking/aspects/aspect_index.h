#pragma once
#include "tainted_ops/tainted_ops.h"

PyObject*
index_aspect(PyObject* result_o,
             const PyObject* candidate_text,
             PyObject* idx,
             const TaintRangeRefs& ranges,
             const TaintRangeMapTypePtr& tx_taint_map);
PyObject*
api_index_aspect(PyObject* self, PyObject* const* args, Py_ssize_t nargs);
