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
all_as_formatted_evidence(const StrType& text, TagMappingMode tag_mapping_mode);

template<class StrType>
StrType
int_as_formatted_evidence(const StrType& text, TaintRangeRefs text_ranges, TagMappingMode tag_mapping_mode);

string
as_formatted_evidence(const string& text,
                      TaintRangeRefs& text_ranges,
                      const optional<TagMappingMode>& tag_mapping_mode = TagMappingMode::Mapper,
                      const optional<const py::dict>& new_ranges = nullopt);

template<class StrType>
StrType
api_as_formatted_evidence(const StrType& text,
                          optional<TaintRangeRefs>& text_ranges,
                          const optional<TagMappingMode>& tag_mapping_mode,
                          const optional<const py::dict>& new_ranges);

template<class StrType>
StrType
api_convert_escaped_text_to_taint_text(const StrType& taint_escaped_text, TaintRangeRefs ranges_orig);

py::bytearray
api_convert_escaped_text_to_taint_text_ba(const py::bytearray& taint_escaped_text, TaintRangeRefs ranges_orig);

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

PyObject*
api_convert_escaped_text_to_taint_text(PyObject* taint_escaped_text,
                                       const TaintRangeRefs& ranges_orig,
                                       PyTextType py_str_type);
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

inline string
get_tag(const string& content)
{
    if (content.empty()) {
        return string(EVIDENCE_MARKS::BLANK);
    }

    auto result = string(EVIDENCE_MARKS::LESS) + content + string(EVIDENCE_MARKS::GREATER);
    return result;
}

inline string
get_default_content(const TaintRangePtr& taint_range)
{
    if (!taint_range->source.name.empty()) {
        return taint_range->source.name;
    }

    return {};
}

// TODO OPTIMIZATION: check if we can use instead a struct object with range_guid_map, new_ranges and default members so
// we dont have to get the keys by string
inline string
mapper_replace(const TaintRangePtr& taint_range, const optional<const py::dict>& new_ranges)
{
    if (!taint_range or !new_ranges) {
        return {};
    }
    py::object o = py::cast(taint_range);

    if (!new_ranges->contains(o)) {
        return {};
    }
    const TaintRange new_range = py::cast<TaintRange>((*new_ranges)[o]);
    return to_string(new_range.get_hash());
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

The user of this macro needs to define the "get_results()" function as a lambda, like:

    auto get_result = [&]() -> PyObject* {
        PyObject* res = do_modulo(candidate_text, candidate_tuple);
        if (res == nullptr) {
            return py_candidate_text.attr("__mod__")(py_candidate_tuple).ptr();
        }
        return res;
    };

Example calling:
    TRY_CATCH_ASPECT("foo_aspect", return result_o, , {  // no cleanup code here, but the comma is still needed
        // code
    });
*/

#define TRY_CATCH_ASPECT(NAME, RETURNRESULT, CLEANUP, ...)                                                             \
    try {                                                                                                              \
        __VA_ARGS__;                                                                                                   \
    } catch (py::error_already_set & e) {                                                                              \
        e.restore();                                                                                                   \
        CLEANUP;                                                                                                       \
        RETURNRESULT;                                                                                                  \
    } catch (const std::exception& e) {                                                                                \
        const std::string error_message = "IAST propagation error in " NAME ". " + std::string(e.what());              \
        iast_taint_log_error(error_message);                                                                           \
        CLEANUP;                                                                                                       \
        RETURNRESULT;                                                                                                  \
    } catch (...) {                                                                                                    \
        const std::string error_message = "Unknown IAST propagation error in " NAME ". ";                              \
        iast_taint_log_error(error_message);                                                                           \
        CLEANUP;                                                                                                       \
        RETURNRESULT;                                                                                                  \
    }
