#pragma once
#include "Initializer/Initializer.h"
#include "TaintTracking/TaintRange.h"
#include "TaintTracking/TaintedObject.h"

namespace py = pybind11;

template<class StrType>
StrType
api_ospathjoin_aspect(StrType& first_part, const py::args& args);

void
pyexport_ospath_aspects(py::module& m);
