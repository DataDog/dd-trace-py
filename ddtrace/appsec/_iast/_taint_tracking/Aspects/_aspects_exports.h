#pragma once
#include "AspectFormat.h"
#include "AspectModulo.h"
#include "AspectSplit.h"
#include "AspectsOsPath.h"
#include "Helpers.h"
#include <pybind11/pybind11.h>

inline void
pyexport_m_aspect_helpers(py::module& m)
{
    py::module m_aspect_helpers = m.def_submodule("aspect_helpers", "Aspect Helpers");
    pyexport_aspect_helpers(m_aspect_helpers);

    py::module m_aspect_format = m.def_submodule("aspect_format", "Aspect Format");
    pyexport_format_aspect(m_aspect_format);

    py::module m_aspects_ospath = m.def_submodule("aspects_ospath", "Aspect os.path.join");
    pyexport_ospath_aspects(m_aspects_ospath);

    py::module m_aspect_split = m.def_submodule("aspect_split", "Aspect split");
    pyexport_aspect_split(m_aspect_split);

    py::module m_aspect_modulo = m.def_submodule("aspect_modulo", "Aspect modulo");
    pyexport_aspect_modulo(m_aspect_modulo);
}
