#pragma once

#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include "TaintTracking/TaintRange.h"

using namespace pybind11::literals;
namespace py = pybind11;

// Calls the specified method and applies the same ranges to the result. Used
// for wrapping simple methods that doesn't change the string size like upper(),
// lower() and similar.
template<class StrType>
StrType
api_common_replace(const py::str& string_method,
                   const StrType& candidate_text,
                   const py::args& args,
                   const py::kwargs& kwargs);

template<class StrType>
StrType
all_as_formatted_evidence(StrType& text, TagMappingMode tag_mapping_mode);

template<class StrType>
StrType
int_as_formatted_evidence(StrType& text, TaintRangeRefs text_ranges, TagMappingMode tag_mapping_mode);

template<class StrType>
StrType
as_formatted_evidence(StrType& text,
                      TaintRangeRefs& text_ranges,
                      const optional<TagMappingMode>& tag_mapping_mode,
                      const optional<const py::dict>& new_ranges);

template<class StrType>
StrType
api_as_formatted_evidence(StrType& text,
                          optional<TaintRangeRefs>& text_ranges,
                          const optional<TagMappingMode>& tag_mapping_mode,
                          const optional<const py::dict>& new_ranges);

py::bytearray
api_convert_escaped_text_to_taint_text_ba(const py::bytearray& taint_escaped_text, TaintRangeRefs ranges_orig);

template<class StrType>
StrType
api_convert_escaped_text_to_taint_text(const StrType& taint_escaped_text, TaintRangeRefs ranges_orig);

template<class StrType>
std::tuple<StrType, TaintRangeRefs>
convert_escaped_text_to_taint_text(const StrType& taint_escaped_text, TaintRangeRefs ranges_orig);

template<class StrType>
bool
set_ranges_on_splitted(const StrType& source_str,
                       const TaintRangeRefs& source_ranges,
                       const py::list& split_result,
                       const TaintRangeMapTypePtr& tx_map,
                       bool include_separator = false);

template<class StrType>
bool
api_set_ranges_on_splitted(const StrType& source_str,
                           const TaintRangeRefs& source_ranges,
                           const py::list& split_result,
                           bool include_separator = false);

bool
has_pyerr();

std::string
has_pyerr_as_string();

void
pyexport_aspect_helpers(py::module& m);

// Yup. This is a macro. It's used to wrap the try-catch block around the aspect code to make sure no exceptions
// escape to the user code causing a SIGABRT. This is a common pattern in the IAST codebase.
// Why not a template function? Because performance. Even a simple one like this cause a huge increase in overhead
// on the fastest aspects:

/*
template<typename Func, typename... Args>
auto
exception_wrapper(Func func, const char* aspect_name, Args... args) -> std::optional<decltype(func(args...))>
{
    try {
        return func(args...);
    } catch (const std::exception& e) {
        iast_taint_log_error(std::string(aspect_name) + ": " + e.what());
    } catch (...) {
        iast_taint_log_error(std::string(aspect_name) + ": Unknown error");
    }
    return std::nullopt;
}
*/

#define TRY_CATCH_ASPECT(NAME, ...)                                                                                    \
    try {                                                                                                              \
        __VA_ARGS__;                                                                                                   \
    } catch (const std::exception& e) {                                                                                \
        const std::string error_message = "IAST propagation error in " NAME ". " + std::string(e.what());              \
        iast_taint_log_error(error_message);                                                                           \
        return result_o;                                                                                               \
    } catch (...) {                                                                                                    \
        const std::string error_message = "Unknown IAST propagation error in " NAME ". ";                              \
        iast_taint_log_error(error_message);                                                                           \
        return result_o;                                                                                               \
    }
