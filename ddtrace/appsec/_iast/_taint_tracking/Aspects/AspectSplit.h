#pragma once

#include "Helpers.h"

template<class StrType>
py::list
api_split_text(const StrType& text, const optional<StrType>& separator, optional<int> maxsplit);

template<class StrType>
py::list
api_rsplit_text(const StrType& text, const optional<StrType>& separator, optional<int> maxsplit);

template<class StrType>
py::list
api_splitlines_text(const StrType& text, bool keepends);

void
pyexport_aspect_split(py::module& m);