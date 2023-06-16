#pragma once

#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include "TaintTracking/Source.h"
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

template<class StrType>
StrType
as_formatted_evidence(const StrType& text,
                      optional<TaintRangeRefs>& text_ranges,
                      const optional<TagMappingMode>& tag_mapping_mode,
                      const optional<const py::dict>& new_ranges);

py::bytearray
_convert_escaped_text_to_taint_text_ba(const py::bytearray& taint_escaped_text, TaintRangeRefs ranges_orig);

template<class StrType>
StrType
_convert_escaped_text_to_taint_text(const StrType& taint_escaped_text, TaintRangeRefs ranges_orig);

template<class StrType>
std::tuple<StrType, TaintRangeRefs>
_convert_escaped_text_to_taint_text_impl(const StrType& taint_escaped_text, TaintRangeRefs ranges_orig);

void
pyexport_aspect_helpers(py::module& m);
