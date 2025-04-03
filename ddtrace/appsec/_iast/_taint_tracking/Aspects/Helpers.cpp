#include "Helpers.h"
#include "Initializer/Initializer.h"
#include "Utils/PythonErrorGuard.h"

#include <algorithm>
#include <iostream>

using namespace pybind11::literals;
namespace py = pybind11;

/**
 * @brief This function is used to get the taint ranges for the given text object.
 *
 * @param string_method The string method to be used.
 * @param candidate_text The text object for which the taint ranges are to be built.
 * @param args The arguments to be passed to the string method.
 * @param kwargs The keyword arguments to be passed to the string method.
 */
template<class StrType>
StrType
api_common_replace(const py::str& string_method,
                   const StrType& candidate_text,
                   const py::args& args,
                   const py::kwargs& kwargs)
{
    const StrType res = py::getattr(candidate_text, string_method)(*args, **kwargs);

    const auto tx_map = Initializer::get_tainting_map();
    if (not tx_map or tx_map->empty()) {
        return res;
    }
    auto [candidate_text_ranges, ranges_error] = get_ranges(candidate_text.ptr(), tx_map);

    if (ranges_error or candidate_text_ranges.empty()) {
        return res;
    }

    set_ranges(res.ptr(), shift_taint_ranges(candidate_text_ranges, 0, -1), tx_map);
    return res;
}

string
as_formatted_evidence(const string& text,
                      TaintRangeRefs& text_ranges,
                      const optional<TagMappingMode>& tag_mapping_mode,
                      const optional<const py::dict>& new_ranges)
{
    if (const auto tx_map = Initializer::get_tainting_map(); !tx_map) {
        return text;
    }

    if (text_ranges.empty() or text.empty()) {
        return text;
    }
    vector<string> res_vector;
    long index = 0;

    sort(text_ranges.begin(), text_ranges.end(), &range_sort);
    auto pystr = py::str(text);

    for (const auto& taint_range : text_ranges) {
        string content;
        // tag_mapping_mode.value throws a compile error: 'value' is unavailable: introduced in macOS 10.13
        if (!tag_mapping_mode or !tag_mapping_mode.has_value() or *tag_mapping_mode == TagMappingMode::Normal) {
            content = get_default_content(taint_range);
        } else
            switch (*tag_mapping_mode) {
                case TagMappingMode::Mapper: {
                    content = to_string(taint_range->get_hash());
                    break;
                }
                case TagMappingMode::Mapper_Replace: {
                    content = mapper_replace(taint_range, new_ranges);
                    break;
                }
                default: {
                    // Nothing
                }
            }

        const auto tag = get_tag(content);

        const auto range_end = taint_range->start + taint_range->length;

        res_vector.push_back(slice_pystr_to_string(pystr, index, taint_range->start));
        res_vector.emplace_back(EVIDENCE_MARKS::START_EVIDENCE);
        res_vector.push_back(tag);
        res_vector.push_back(slice_pystr_to_string(pystr, taint_range->start, range_end));
        res_vector.push_back(tag);
        res_vector.emplace_back(EVIDENCE_MARKS::END_EVIDENCE);

        index = range_end;
    }
    res_vector.push_back(slice_pystr_to_string(pystr, index, py::len(pystr)));
    ostringstream oss;
    for (const auto& str : res_vector) {
        oss << str;
    }

    return oss.str();
}

template<class StrType>
StrType
api_as_formatted_evidence(const StrType& text,
                          optional<const TaintRangeRefs>& text_ranges,
                          const optional<TagMappingMode>& tag_mapping_mode,
                          const optional<const py::dict>& new_ranges)
{
    if (const auto tx_map = Initializer::get_tainting_map(); !tx_map) {
        return text;
    }

    TaintRangeRefs _ranges;
    if (!text_ranges or !text_ranges.has_value()) {
        _ranges = api_get_ranges(text);
    } else {
        // text_ranges.value throws a compile error: 'value' is unavailable: introduced in macOS 10.13
        _ranges = *text_ranges;
    }
    return StrType(as_formatted_evidence(AnyTextObjectToString(text), _ranges, tag_mapping_mode, new_ranges));
}

py::bytearray
api_convert_escaped_text_to_taint_text(const py::bytearray& taint_escaped_text, const TaintRangeRefs& ranges_orig)
{

    const auto tx_map = Initializer::get_tainting_map();

    const py::bytes bytes_text = py::bytes() + taint_escaped_text;

    const std::tuple result = convert_escaped_text_to_taint_text<py::bytes>(bytes_text, ranges_orig);
    PyObject* new_result = new_pyobject_id((py::bytearray() + get<0>(result)).ptr());
    set_ranges(new_result, get<1>(result), tx_map);
    return py::reinterpret_steal<py::bytearray>(new_result);
}

template<class StrType>
StrType
api_convert_escaped_text_to_taint_text(const StrType& taint_escaped_text, const TaintRangeRefs& ranges_orig)
{
    const auto tx_map = Initializer::get_tainting_map();

    auto [result_text, result_ranges] = convert_escaped_text_to_taint_text<StrType>(taint_escaped_text, ranges_orig);
    PyObject* new_result = new_pyobject_id(result_text.ptr());
    set_ranges(new_result, result_ranges, tx_map);
    return py::reinterpret_steal<StrType>(new_result);
}

PyObject*
api_convert_escaped_text_to_taint_text(PyObject* taint_escaped_text,
                                       const TaintRangeRefs& ranges_orig,
                                       const PyTextType py_str_type)
{
    if (taint_escaped_text == nullptr or ranges_orig.empty()) {
        return taint_escaped_text;
    }

    const auto text_pyobj_opt = PyObjectToPyText(taint_escaped_text);
    if (!text_pyobj_opt.has_value()) {
        return taint_escaped_text;
    }

    switch (py_str_type) {
        case PyTextType::UNICODE: {
            // text_pyobj_opt.value throws a compile error: 'value' is unavailable: introduced in macOS 10.13
            const auto text_str = py::reinterpret_borrow<py::str>(*text_pyobj_opt);
            auto obj = api_convert_escaped_text_to_taint_text<py::str>(text_str, ranges_orig);
            Py_INCREF(obj.ptr());
            return obj.ptr();
        }
        case PyTextType::BYTES: {
            // text_pyobj_opt.value throws a compile error: 'value' is unavailable: introduced in macOS 10.13
            const auto text_bytes = py::reinterpret_borrow<py::bytes>(*text_pyobj_opt);
            auto obj = api_convert_escaped_text_to_taint_text<py::bytes>(text_bytes, ranges_orig);
            Py_INCREF(obj.ptr());
            return obj.ptr();
        }
        case PyTextType::BYTEARRAY: {
            // text_pyobj_opt.value throws a compile error: 'value' is unavailable: introduced in macOS 10.13
            const auto text_bytearray = py::reinterpret_borrow<py::bytearray>(*text_pyobj_opt);
            auto obj = api_convert_escaped_text_to_taint_text<py::bytearray>(text_bytearray, ranges_orig);
            Py_INCREF(obj.ptr());
            return obj.ptr();
        }
        default:
            return taint_escaped_text;
    }
}

template<class StrType>
std::tuple<StrType, TaintRangeRefs>
convert_escaped_text_to_taint_text(const StrType& taint_escaped_text, const TaintRangeRefs& ranges_orig)
{
    string result;
    string startswith_element{ ":" };

    string taint_escaped_string = py::cast<string>(taint_escaped_text);
    vector<string> texts_and_marks = split_taints(taint_escaped_string);
    optional<TaintRangeRefs> optional_ranges_orig = ranges_orig;

    vector<tuple<string, int>> context_stack;
    int length = 0;
    int end = 0;
    TaintRangeRefs ranges;

    int latest_end = -1;
    int index = 0;
    int start;
    int prev_context_pos;
    string id_evidence;

    for (string const& element : texts_and_marks) {
        if (index % 2 == 0) {
            result += element;
            length = py::len(StrType(element));
            end += length;
            index++;
            continue;
        }
        if (element.rfind(startswith_element, 0) == 0) {
            id_evidence = element.substr(4, element.length() - 5);
            if (auto range_by_id = get_range_by_hash(getNum(id_evidence), optional_ranges_orig);
                range_by_id == nullptr) {
                result += element;
                length = py::len(StrType(element));
                end += length;
                index++;
                continue;
            }

            if (!context_stack.empty()) {
                auto previous_context = context_stack.back();

                prev_context_pos = get<1>(previous_context);
                if (prev_context_pos > latest_end) {
                    start = prev_context_pos;
                } else {
                    start = latest_end;
                }

                if (start != end) {
                    id_evidence = get<0>(previous_context);
                    const shared_ptr<TaintRange>& original_range =
                      get_range_by_hash(getNum(id_evidence), optional_ranges_orig);
                    ranges.emplace_back(initializer->allocate_taint_range(start, length, original_range->source));
                }
                latest_end = end;
            }
            id_evidence = element.substr(4, element.length() - 5);
            start = end;
            context_stack.emplace_back(id_evidence, start);
        } else {
            id_evidence = element.substr(1, element.length() - 5);
            if (auto range_by_id = get_range_by_hash(getNum(id_evidence), optional_ranges_orig);
                range_by_id == nullptr) {
                result += element;
                length = py::len(StrType(element));
                end += length;
                index++;
                continue;
            }

            auto context = context_stack.back();
            context_stack.pop_back();
            prev_context_pos = get<1>(context);
            if (prev_context_pos > latest_end) {
                start = prev_context_pos;
            } else {
                start = latest_end;
            }

            if (start != end) {
                id_evidence = get<0>(context);
                const shared_ptr<TaintRange>& original_range =
                  get_range_by_hash(getNum(id_evidence), optional_ranges_orig);
                ranges.emplace_back(initializer->allocate_taint_range(start, end - start, original_range->source));
            }
            latest_end = end;
        }
        index++;
    }
    return { StrType(result), ranges };
}

/**
 * @brief This function takes the ranges of a string splitted (as in string.split or rsplit or os.path.split) and
 * applies the ranges of the original string to the splitted parts with updated offsets.
 *
 * @param source_str: The original string that was splitted.
 * @param source_ranges: The ranges of the original string.
 * @param split_result: The splitted parts of the original string.
 * @param tx_map: The taint map to apply the ranges.
 * @param include_separator: If the separator should be included in the splitted parts.
 */
bool
set_ranges_on_splitted(const py::object& source_str,
                       const TaintRangeRefs& source_ranges,
                       const py::list& split_result,
                       const TaintRangeMapTypePtr& tx_map,
                       bool include_separator)
{
    RANGE_START offset = 0;
    bool some_set = false;

    if (source_ranges.empty() or py::len(split_result) == 0 or py::len(source_str) == 0 or not tx_map or
        tx_map->empty()) {
        return false;
    }

    for (const auto& item : split_result) {
        if (not is_text(item.ptr()) or py::len(item) == 0) {
            continue;
        }
        TaintRangeRefs item_ranges;
        RANGE_START part_len = py::len(item);
        RANGE_START part_start = offset;
        RANGE_START part_end = part_start + part_len;

        // bool first = true;
        for (const auto& range : source_ranges) {
            RANGE_START range_start = range->start;
            RANGE_START range_end = range->start + range->length;

            // Check for overlap
            if (range_start < part_end && range_end > part_start) {
                RANGE_START new_start = std::max(range_start - part_start, 0L);
                RANGE_START new_end = std::min(range_end - part_start, part_len);
                RANGE_START new_length = std::min(new_end - new_start, part_len);

                if (new_length > 0) {
                    item_ranges.emplace_back(initializer->allocate_taint_range(new_start, new_length, range->source));
                }
            }
        }

        if (not item_ranges.empty()) {
            set_ranges(item.ptr(), item_ranges, tx_map);
            some_set = true;
        }
        offset += part_len;

        if (!include_separator) {
            offset += 1;
        }
    }

    return some_set;
}

template<class StrType>
bool
api_set_ranges_on_splitted(const StrType& source_str,
                           const TaintRangeRefs& source_ranges,
                           const py::list& split_result,
                           bool include_separator)
{
    const auto tx_map = Initializer::get_tainting_map();
    if (not tx_map or tx_map->empty()) {
        return false;
    }
    return set_ranges_on_splitted(source_str, source_ranges, split_result, tx_map, include_separator);
}

bool
has_pyerr()
{
    PythonErrorGuard error_guard;
    return error_guard.has_error();
}

std::string
has_pyerr_as_string()
{
    PythonErrorGuard error_guard;
    if (not error_guard.has_error()) {
        return {};
    }

    return error_guard.error_as_stdstring();
}

py::str
has_pyerr_as_pystr()
{
    PythonErrorGuard error_guard;
    if (not error_guard.has_error()) {
        return {};
    }

    return error_guard.error_as_pystr();
}

void
pyexport_aspect_helpers(py::module& m)
{
    m.def("common_replace",
          &api_common_replace<py::bytes>,
          "string_method"_a,
          "candidate_text"_a,
          py::return_value_policy::move);
    m.def("common_replace",
          &api_common_replace<py::str>,
          "string_method"_a,
          "candidate_text"_a,
          py::return_value_policy::move);
    m.def("common_replace",
          &api_common_replace<py::bytearray>,
          "string_method"_a,
          "candidate_text"_a,
          py::return_value_policy::move);
    m.def("set_ranges_on_splitted",
          &api_set_ranges_on_splitted<py::bytes>,
          "source_str"_a,
          "source_ranges"_a,
          "split_result"_a,
          // cppcheck-suppress assignBoolToPointer
          "include_separator"_a = false);
    m.def("set_ranges_on_splitted",
          &api_set_ranges_on_splitted<py::str>,
          "source_str"_a,
          "source_ranges"_a,
          "split_result"_a,
          // cppcheck-suppress assignBoolToPointer
          "include_separator"_a = false);
    m.def("set_ranges_on_splitted",
          &api_set_ranges_on_splitted<py::bytearray>,
          "source_str"_a,
          "source_ranges"_a,
          "split_result"_a,
          // cppcheck-suppress assignBoolToPointer
          "include_separator"_a = false);
    m.def("as_formatted_evidence",
          &api_as_formatted_evidence<py::bytes>,
          "text"_a,
          "text_ranges"_a = nullopt,
          "tag_mapping_function"_a = nullopt,
          "new_ranges"_a = nullopt,
          py::return_value_policy::move);
    m.def("as_formatted_evidence",
          &api_as_formatted_evidence<py::str>,
          "text"_a,
          "text_ranges"_a = nullopt,
          "tag_mapping_function"_a = nullopt,
          "new_ranges"_a = nullopt,
          py::return_value_policy::move);
    m.def("as_formatted_evidence",
          &api_as_formatted_evidence<py::bytearray>,
          "text"_a,
          "text_ranges"_a = nullopt,
          "tag_mapping_function"_a = nullopt,
          "new_ranges"_a = nullopt,
          py::return_value_policy::move);
    m.def("_convert_escaped_text_to_tainted_text",
          &api_convert_escaped_text_to_taint_text<py::bytes>,
          "taint_escaped_text"_a,
          "ranges_orig"_a,
          py::return_value_policy::move);
    m.def("_convert_escaped_text_to_tainted_text",
          &api_convert_escaped_text_to_taint_text<py::str>,
          "taint_escaped_text"_a,
          "ranges_orig"_a,
          py::return_value_policy::move);
    m.def("_convert_escaped_text_to_tainted_text",
          &api_convert_escaped_text_to_taint_text<py::bytearray>,
          "taint_escaped_text"_a,
          "ranges_orig"_a,
          py::return_value_policy::move);
    m.def("parse_params", &parse_param);
    m.def("has_pyerr", &has_pyerr);
    m.def("has_pyerr_as_string", &has_pyerr_as_string);
}
