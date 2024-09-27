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
                      const optional<TagMappingMode>& tag_mapping_mode = TagMappingMode::Mapper,
                      const optional<const py::dict>& new_ranges = nullopt);

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

bool
set_ranges_on_splitted(const py::object& source_str,
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

struct EVIDENCE_MARKS
{
    static constexpr const char* BLANK = "";
    static constexpr const char* START_EVIDENCE = ":+-";
    static constexpr const char* END_EVIDENCE = "-+:";
    static constexpr const char* LESS = "<";
    static constexpr const char* GREATER = ">";
};

inline bool
range_sort(const TaintRangePtr& t1, const TaintRangePtr& t2)
{
    return t1->start < t2->start;
}

template<class StrType>
static StrType
get_tag(const py::object& content)
{
    if (content.is_none()) {
        return StrType(EVIDENCE_MARKS::BLANK);
    }

    if (py::isinstance<py::str>(StrType(EVIDENCE_MARKS::LESS))) {
        return StrType(EVIDENCE_MARKS::LESS) + content.cast<py::str>() + StrType(EVIDENCE_MARKS::GREATER);
    }
    return StrType(EVIDENCE_MARKS::LESS) + py::bytes(content.cast<py::str>()) + StrType(EVIDENCE_MARKS::GREATER);
}

inline py::object
get_default_content(const TaintRangePtr& taint_range)
{
    if (!taint_range->source.name.empty()) {
        return py::str(taint_range->source.name);
    }

    return py::cast<py::none>(Py_None);
}

// TODO OPTIMIZATION: check if we can use instead a struct object with range_guid_map, new_ranges and default members so
// we dont have to get the keys by string
inline py::object
mapper_replace(const TaintRangePtr& taint_range, const optional<const py::dict>& new_ranges)
{
    if (!taint_range or !new_ranges) {
        return py::none{};
    }
    py::object o = py::cast(taint_range);

    if (!new_ranges->contains(o)) {
        return py::none{};
    }
    const TaintRange new_range = py::cast<TaintRange>((*new_ranges)[o]);
    return py::int_(new_range.get_hash());
}

// TODO OPTIMIZATION: Remove py::types once this isn't used in Python
template<class StrType>
StrType
as_formatted_evidence(StrType& text,
                      TaintRangeRefs& text_ranges,
                      const optional<TagMappingMode>& tag_mapping_mode,
                      const optional<const py::dict>& new_ranges)
{
    if (text_ranges.empty()) {
        return text;
    }
    vector<StrType> res_vector;
    long index = 0;

    sort(text_ranges.begin(), text_ranges.end(), &range_sort);
    for (const auto& taint_range : text_ranges) {
        py::object content;
        if (!tag_mapping_mode) {
            content = get_default_content(taint_range);
        } else
            switch (*tag_mapping_mode) {
                case TagMappingMode::Mapper:
                    content = py::int_(taint_range->get_hash());
                    break;
                case TagMappingMode::Mapper_Replace:
                    content = mapper_replace(taint_range, new_ranges);
                    break;
                default: {
                    // Nothing
                }
            }
        const auto tag = get_tag<StrType>(content);

        const auto range_end = taint_range->start + taint_range->length;

        res_vector.push_back(text[py::slice(py::int_{ index }, py::int_{ taint_range->start }, nullptr)]);
        res_vector.push_back(StrType(EVIDENCE_MARKS::START_EVIDENCE));
        res_vector.push_back(tag);
        res_vector.push_back(text[py::slice(py::int_{ taint_range->start }, py::int_{ range_end }, nullptr)]);
        res_vector.push_back(tag);
        res_vector.push_back(StrType(EVIDENCE_MARKS::END_EVIDENCE));

        index = range_end;
    }
    res_vector.push_back(text[py::slice(py::int_(index), nullptr, nullptr)]);
    return StrType(EVIDENCE_MARKS::BLANK).attr("join")(res_vector);
}

inline PyObject*
process_flag_added_args(PyObject* orig_function, const int flag_added_args, PyObject* args, PyObject* kwargs)
{
    // If orig_function is not None and not the built-in str, bytes, or bytearray, slice args

    if (const auto orig_function_type = Py_TYPE(orig_function);
        orig_function != Py_None && orig_function_type != &PyUnicode_Type && orig_function_type != &PyByteArray_Type &&
        orig_function_type != &PyBytes_Type) {

        if (flag_added_args > 0) {
            const Py_ssize_t num_args = PyTuple_Size(args);
            PyObject* sliced_args = PyTuple_New(num_args - flag_added_args);
            for (Py_ssize_t i = 0; i < num_args - flag_added_args; ++i) {
                // PyTuple_SET_ITEM(sliced_args, i, PyTuple_GET_ITEM(args, i + flag_added_args));
                PyObject* item = PyTuple_GetItem(args, i + flag_added_args);
                Py_INCREF(item);                        // Increase the reference count here
                PyTuple_SET_ITEM(sliced_args, i, item); // Steal the reference
            }
            // Call the original function with the sliced args and return its result
            PyObject* result = PyObject_Call(orig_function, sliced_args, kwargs);
            Py_DECREF(sliced_args);
            return result;
        }
        // Else: call the original function with all args if no slicing is needed
        return PyObject_Call(orig_function, args, kwargs);
    }

    // If orig_function is None or one of the built-in types, just return args for further processing
    // Note: it the caller assigns the PyObject* to a py::object or derivate like with:
    // auto foo py::reinterpret_borrow<py::list>(result_or_args);
    // Then you don't need to Py_INCREF the resulted value. But if it's used as a PyObject*, then you need
    // to do it.
    return args;
}

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

#define TRY_CATCH_ASPECT(NAME, CLEANUP, ...)                                                                           \
    try {                                                                                                              \
        __VA_ARGS__;                                                                                                   \
    } catch (const std::exception& e) {                                                                                \
        const std::string error_message = NAME ". " + std::string(e.what());                                           \
        iast_taint_log_error(error_message);                                                                           \
        CLEANUP;                                                                                                       \
        return result_o;                                                                                               \
    } catch (...) {                                                                                                    \
        const std::string error_message = "Unknown IAST propagation error in " NAME ". ";                              \
        iast_taint_log_error(error_message);                                                                           \
        CLEANUP;                                                                                                       \
        return result_o;                                                                                               \
    }
