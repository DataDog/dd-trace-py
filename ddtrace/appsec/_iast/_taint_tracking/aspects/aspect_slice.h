#pragma once
#include "api/safe_context.h"
#include "api/safe_initializer.h"
#include "api/utils.h"
#include "helpers.h"
#include "tainted_ops/tainted_ops.h"

PyObject*
api_slice_aspect(PyObject* self, PyObject* const* args, Py_ssize_t nargs);
