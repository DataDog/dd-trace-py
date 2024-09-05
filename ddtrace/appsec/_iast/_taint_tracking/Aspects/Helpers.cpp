#include "Helpers.h"
#include "Initializer/Initializer.h"
#include <algorithm>
#include <regex>

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

template<class StrType>
StrType
all_as_formatted_evidence(StrType& text, TagMappingMode tag_mapping_mode)
{
    TaintRangeRefs text_ranges = api_get_ranges(text);
    return as_formatted_evidence<StrType>(text, text_ranges, tag_mapping_mode, nullopt);
}

template<class StrType>
StrType
int_as_formatted_evidence(StrType& text, TaintRangeRefs text_ranges, TagMappingMode tag_mapping_mode)
{
    return as_formatted_evidence<StrType>(text, text_ranges, tag_mapping_mode, nullopt);
}

template<class StrType>
StrType
api_as_formatted_evidence(StrType& text,
                          optional<TaintRangeRefs>& text_ranges,
                          const optional<TagMappingMode>& tag_mapping_mode,
                          const optional<const py::dict>& new_ranges)
{
    TaintRangeRefs _ranges;
    if (!text_ranges) {
        _ranges = api_get_ranges(text);
    } else {
        _ranges = text_ranges.value();
    }
    return as_formatted_evidence<StrType>(text, _ranges, tag_mapping_mode, new_ranges);
}

vector<string>
split_taints(const string& str_to_split)
{
    const std::regex rgx(R"((:\+-(<[0-9.a-z\-]+>)?|(<[0-9.a-z\-]+>)?-\+:))");
    std::sregex_token_iterator iter(str_to_split.begin(), str_to_split.end(), rgx, { -1, 0 });
    vector<string> res;

    for (const std::sregex_token_iterator end; iter != end; ++iter) {
        res.push_back(*iter);
    }

    return res;
}

py::bytearray
api_convert_escaped_text_to_taint_text(const py::bytearray& taint_escaped_text, TaintRangeRefs ranges_orig)
{
    const auto tx_map = Initializer::get_tainting_map();

    const py::bytes bytes_text = py::bytes() + taint_escaped_text;

    const std::tuple result = convert_escaped_text_to_taint_text<py::bytes>(bytes_text, std::move(ranges_orig));
    PyObject* new_result = new_pyobject_id((py::bytearray() + get<0>(result)).ptr());
    set_ranges(new_result, get<1>(result), tx_map);
    return py::reinterpret_steal<py::bytearray>(new_result);
}

template<class StrType>
StrType
api_convert_escaped_text_to_taint_text(const StrType& taint_escaped_text, TaintRangeRefs ranges_orig)
{
    const auto tx_map = Initializer::get_tainting_map();

    auto [result_text, result_ranges] = convert_escaped_text_to_taint_text<StrType>(taint_escaped_text, ranges_orig);
    PyObject* new_result = new_pyobject_id(result_text.ptr());
    set_ranges(new_result, result_ranges, tx_map);
    return py::reinterpret_steal<StrType>(new_result);
}

unsigned long int
getNum(const std::string& s)
{
    unsigned int n = -1;
    try {
        n = std::stoul(s, nullptr, 10);
        if (errno != 0) {
            PyErr_Print();
        }
    } catch (std::exception& e) {
        // throw std::invalid_argument("Value is too big");
        PyErr_Print();
    }
    return n;
}

template<class StrType>
std::tuple<StrType, TaintRangeRefs>
convert_escaped_text_to_taint_text(const StrType& taint_escaped_text, TaintRangeRefs ranges_orig)
{
    string result;
    string startswith_element{ ":" };

    string taint_escaped_string = py::cast<string>(taint_escaped_text);
    vector<string> texts_and_marks = split_taints(taint_escaped_string);
    optional<TaintRangeRefs> optional_ranges_orig = ranges_orig;

    vector<tuple<string, int>> context_stack;
    int length, end = 0;
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
    bool some_set = false;

    // Some quick shortcuts
    if (source_ranges.empty() or py::len(split_result) == 0 or py::len(source_str) == 0 or not tx_map or
        tx_map->empty()) {
        return false;
    }

    RANGE_START offset = 0;
    std::string c_source_str = py::cast<std::string>(source_str);
    const auto separator_increase = static_cast<int>(not include_separator);

    for (const auto& item : split_result) {
        if (not is_text(item.ptr()) or py::len(item) == 0) {
            continue;
        }
        auto c_item = py::cast<std::string>(item);
        TaintRangeRefs item_ranges;

        // Find the item in the source_str.
        const auto start = static_cast<RANGE_START>(c_source_str.find(c_item, offset));
        if (start == -1) {
            continue;
        }
        const auto end = static_cast<RANGE_START>(start + c_item.length());

        // Find what source_ranges match these positions and create a new range with the start and len updated.
        for (const auto& range : source_ranges) {
            if (const auto range_end_abs = range->start + range->length; range->start < end && range_end_abs > start) {
                // Create a new range with the updated start
                const auto new_range_start = std::max(range->start - offset, 0L);
                const auto new_range_length =
                  std::min(end - start, (range->length - std::max(0L, offset - range->start)));
                item_ranges.emplace_back(
                  initializer->allocate_taint_range(new_range_start, new_range_length, range->source));
            }
        }
        if (not item_ranges.empty()) {
            set_ranges(item.ptr(), item_ranges, tx_map);
            some_set = true;
        }

        offset += py::len(item) + separator_increase;
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

py::object
parse_params(size_t position,
             const char* keyword_name,
             const py::object& default_value,
             const py::args& args,
             const py::kwargs& kwargs)
{
    if (args.size() >= position + 1) {
        return args[position];
    }
    if (kwargs && kwargs.contains(keyword_name)) {
        return kwargs[keyword_name];
    }
    return default_value;
}

bool
has_pyerr()
{
    return !has_pyerr_as_string().empty();
}

std::string
has_pyerr_as_string()
{

    if (PyErr_Occurred()) {
        PyObject *extype, *value, *traceback;
        PyErr_Fetch(&extype, &value, &traceback);
        PyErr_NormalizeException(&extype, &value, &traceback);
        const auto exception_msg_as_pystr = py::str(PyObject_Str(value));
        const auto exception_msg_as_string = std::string(PyUnicode_AsUTF8(exception_msg_as_pystr.ptr()));
        py::set_error(extype, exception_msg_as_pystr);
        Py_DecRef(extype);
        Py_DecRef(value);
        Py_DecRef(traceback);
        return exception_msg_as_string;
    }

    return {};
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
    m.def("_all_as_formatted_evidence",
          &all_as_formatted_evidence<py::str>,
          "text"_a,
          "tag_mapping_function"_a = nullopt,
          py::return_value_policy::move);
    m.def("_int_as_formatted_evidence",
          &int_as_formatted_evidence<py::str>,
          "text"_a,
          "text_ranges"_a = nullopt,
          "tag_mapping_function"_a = nullopt,
          py::return_value_policy::move);
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
          &api_convert_escaped_text_to_taint_text<py::bytearray>,
          "taint_escaped_text"_a,
          "ranges_orig"_a,
          py::return_value_policy::move);
    m.def("_convert_escaped_text_to_tainted_text",
          &api_convert_escaped_text_to_taint_text<py::str>,
          "taint_escaped_text"_a,
          "ranges_orig"_a,
          py::return_value_policy::move);
    m.def("parse_params", &parse_params);
    m.def("has_pyerr", &has_pyerr);
    m.def("has_pyerr_as_string", &has_pyerr_as_string);
}
