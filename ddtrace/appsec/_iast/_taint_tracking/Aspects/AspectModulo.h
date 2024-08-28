#pragma once
#include "Initializer/Initializer.h"

namespace py = pybind11;

template<class StrType>
StrType
api_modulo_aspect(StrType candidate_text, py::object candidate_tuple);

void
pyexport_aspect_modulo(py::module& m);
