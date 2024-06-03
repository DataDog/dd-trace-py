#pragma once
#include "Aspects/Helpers.h"
#include "Initializer/Initializer.h"

template<class StrType>
StrType
api_format_aspect(StrType& candidate_text,
                  const py::tuple& parameter_list,
                  const py::args& args,
                  const py::kwargs& kwargs);

void
pyexport_format_aspect(py::module& m);