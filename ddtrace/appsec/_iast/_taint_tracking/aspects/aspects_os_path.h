#pragma once
#include "initializer/initializer.h"

namespace py = pybind11;

template<class StrType>
StrType
api_ospathjoin_aspect(StrType& first_part, const py::args& args);

template<class StrType>
StrType
api_ospathbasename_aspect(const StrType& path);

template<class StrType>
StrType
api_ospathdirname_aspect(const StrType& path);

template<class StrType>
py::tuple
api_ospathsplit_aspect(const StrType& path);

template<class StrType>
py::tuple
api_ospathsplitext_aspect(const StrType& path);

template<class StrType>
py::tuple
api_ospathsplitdrive_aspect(const StrType& path);

template<class StrType>
py::tuple
api_ospathsplitroot_aspect(const StrType& path);

template<class StrType>
StrType
api_ospathnormcase_aspect(const StrType& path);

void
pyexport_ospath_aspects(py::module& m);
