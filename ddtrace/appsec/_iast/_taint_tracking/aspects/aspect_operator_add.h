#pragma once
#include "api/safe_context.h"
#include "api/safe_initializer.h"
#include "api/utils.h"
#include "helpers.h"

PyObject*
api_add_aspect(PyObject* self, PyObject* const* args, Py_ssize_t nargs);

PyObject*
api_add_inplace_aspect(PyObject* self, PyObject* const* args, Py_ssize_t nargs);
