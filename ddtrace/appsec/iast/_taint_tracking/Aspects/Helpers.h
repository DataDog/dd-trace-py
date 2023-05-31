#pragma once

#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include "TaintTracking/TaintRange.h"

using namespace pybind11::literals;
namespace py = pybind11;

size_t
get_pyobject_size(PyObject* obj);

// Calls the specified method and applies the same ranges to the result. Used
// for wrapping simple methods that doesn't change the string size like upper(),
// lower() and similar.
template<class StrType>
StrType
common_replace(const py::str& string_method,
               const StrType& candidate_text,
               const py::args& args,
               const py::kwargs& kwargs);

void
pyexport_aspect_helpers(py::module& m);
