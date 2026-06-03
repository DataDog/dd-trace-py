#pragma once

#include "api/safe_context.h"
#include "api/safe_initializer.h"

PyObject*
api_extend_aspect(PyObject* self, PyObject* const* args, Py_ssize_t nargs);
