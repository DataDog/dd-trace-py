#pragma once
#include "api/safe_context.h"
#include "api/safe_initializer.h"
#include "api/utils.h"
#include "helpers.h"
#include "tainted_ops/tainted_ops.h"
#include "utils/string_utils.h"

PyObject*
index_aspect(PyObject* result_o,
             const PyObject* candidate_text,
             PyObject* idx,
             const TaintRangeRefs& ranges,
             const TaintedObjectMapTypePtr& tx_taint_map);
PyObject*
api_index_aspect(PyObject* self, PyObject* const* args, Py_ssize_t nargs);
