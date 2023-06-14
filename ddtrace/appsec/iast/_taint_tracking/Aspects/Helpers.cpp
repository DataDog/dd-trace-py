#include "Helpers.h"
#include <iostream> // JJJ: remove
#include <ostream>
#include <regex>

using namespace pybind11::literals;
namespace py = pybind11;

size_t
get_pyobject_size(PyObject* obj)
{
    size_t len_candidate_text{ 0 };
    if (PyUnicode_Check(obj)) {
        len_candidate_text = PyUnicode_GET_LENGTH(obj);
    } else if (PyBytes_Check(obj)) {
        len_candidate_text = PyBytes_Size(obj);
    } else if (PyByteArray_Check(obj)) {
        len_candidate_text = PyByteArray_Size(obj);
    }
    return len_candidate_text;
}

template<class StrType>
StrType
common_replace(const py::str& string_method,
               const StrType& candidate_text,
               const py::args& args,
               const py::kwargs& kwargs)
{
    TaintRangeRefs candidate_text_ranges{ get_ranges(candidate_text.ptr()) };

    StrType res = py::getattr(candidate_text, string_method)(*args, **kwargs);
    if (candidate_text_ranges.empty()) {
        return res;
    }

    set_ranges(res.ptr(), api_shift_taint_ranges(candidate_text_ranges, 0));
    return res;
}

struct EVIDENCE_MARKS
{
    static constexpr const char* BLANK = "";
    static constexpr const char* START_EVIDENCE = ":+-";
    static constexpr const char* END_EVIDENCE = "-+:";
    static constexpr const char* LESS = "<";
    static constexpr const char* GREATER = ">";
};

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

// TODO OPTIMIZATION: check if we can use instead a struct object with range_guid_map, new_ranges and default members so
// we dont have to get the keys by string
static py::object
mapper_replace(const TaintRangePtr& taint_range, const optional<const py::dict>& new_ranges)
{
    if (!taint_range or !new_ranges) {
        return py::none{};
    }
    py::object o = py::cast(taint_range);

    if (!new_ranges->contains(o)) {
        return py::none{};
    }
    TaintRange new_range = py::cast<TaintRange>((*new_ranges)[o]);
    return py::int_(new_range.get_hash());
}

py::object
get_default_content(const TaintRangePtr& taint_range)
{
    if (!taint_range->source->name.empty()) {
        return py::str(taint_range->source->name);
    }

    return py::cast<py::none>(Py_None);
}

bool
range_sort(const TaintRangePtr& t1, const TaintRangePtr& t2)
{
    return t1->start < t2->start;
}

// TODO OPTIMIZATION: Remove py::types once this isn't used in Python
template<class StrType>
StrType
as_formatted_evidence(const StrType& text,
                      optional<TaintRangeRefs>& text_ranges,
                      const optional<TagMappingMode>& tag_mapping_mode,
                      const optional<const py::dict>& new_ranges)
{
    if (!text_ranges) {
        text_ranges = api_get_ranges(text);
    }
    if (text_ranges->empty()) {
        return text;
    }
    vector<StrType> res_vector;
    long index = 0;

    sort(text_ranges->begin(), text_ranges->end(), &range_sort);
    for (const auto& taint_range : *text_ranges) {
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
        StrType tag = get_tag<StrType>(content);

        auto range_end = taint_range->start + taint_range->length;

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

vector<string>
split_taints(const string& str_to_split)
{
    std::regex rgx(R"((:\+-(<[0-9.a-z\-]+>)?|(<[0-9.a-z\-]+>)?-\+:))");
    std::sregex_token_iterator iter(str_to_split.begin(), str_to_split.end(), rgx, { -1, 0 });
    std::sregex_token_iterator end;
    vector<string> res;

    for (; iter != end; ++iter) {
        res.push_back(*iter);
    }

    return res;
}

py::bytearray
_convert_escaped_text_to_taint_text_ba(const py::bytearray& taint_escaped_text, TaintRangeRefs ranges_orig)
{
    py::bytes bytes_text = py::bytes() + taint_escaped_text;

    std::tuple result = _convert_escaped_text_to_taint_text_impl<py::bytes>(bytes_text, ranges_orig);
    py::bytearray result_new_id = copy_string_new_id(py::bytearray() + get<0>(result));
    set_ranges(result_new_id.ptr(), get<1>(result));
    return result_new_id;
}

template<class StrType>
StrType
_convert_escaped_text_to_taint_text(const StrType& taint_escaped_text, TaintRangeRefs ranges_orig)
{
    std::tuple result = _convert_escaped_text_to_taint_text_impl<StrType>(taint_escaped_text, ranges_orig);
    StrType result_text = get<0>(result);
    TaintRangeRefs result_ranges = get<1>(result);
    StrType result_new_id = copy_string_new_id(result_text);
    set_ranges(result_new_id.ptr(), result_ranges);
    return result_new_id;
}

unsigned long int
getNum(std::string s)
{
    unsigned int n = -1;
    try {
        n = std::stoul(s, nullptr, 10);
        if (errno != 0) {
            std::cout << "ERROR" << '\n';
        }
    } catch (std::exception& e) {
        // throw std::invalid_argument("Value is too big");
        std::cout << "Invalid value: " << s << '\n';
    }
    return n;
}

template<class StrType>
std::tuple<StrType, TaintRangeRefs>
_convert_escaped_text_to_taint_text_impl(const StrType& taint_escaped_text, TaintRangeRefs ranges_orig)
{
    string result{ u8"" };
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
        bool is_content = index % 2 == 0;
        if (is_content) {
            result += element;
            length = py::len(StrType(element));
            end += length;
            index++;
            continue;
        }
        if (element.rfind(startswith_element, 0) == 0) {
            id_evidence = element.substr(4, element.length() - 5);
            auto range_by_id = get_range_by_hash(getNum(id_evidence), optional_ranges_orig);
            if (range_by_id == nullptr) {
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
                    ranges.emplace_back(make_shared<TaintRange>(TaintRange(start, length, original_range->source)));
                }
                latest_end = end;
            }
            id_evidence = element.substr(4, element.length() - 5);
            start = end;
            context_stack.push_back({ id_evidence, start });
        } else {
            id_evidence = element.substr(1, element.length() - 5);
            auto range_by_id = get_range_by_hash(getNum(id_evidence), optional_ranges_orig);
            if (range_by_id == nullptr) {
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
                ranges.emplace_back(make_shared<TaintRange>(TaintRange(start, end - start, original_range->source)));
            }
            latest_end = end;
        }
        index++;
    }
    return { StrType(result), ranges };
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
    } else if (kwargs && kwargs.contains(keyword_name)) {
        return kwargs[keyword_name];
    }
    return default_value;
}

void
pyexport_aspect_helpers(py::module& m)
{
    m.def("common_replace", &common_replace<py::bytes>, "string_method"_a, "candidate_text"_a);
    m.def("common_replace", &common_replace<py::str>, "string_method"_a, "candidate_text"_a);
    m.def("common_replace", &common_replace<py::bytearray>, "string_method"_a, "candidate_text"_a);
    m.def("as_formatted_evidence",
          &as_formatted_evidence<py::bytes>,
          "text"_a,
          "text_ranges"_a = nullopt,
          "tag_mapping_function"_a = nullopt,
          "new_ranges"_a = nullopt);
    m.def("as_formatted_evidence",
          &as_formatted_evidence<py::str>,
          "text"_a,
          "text_ranges"_a = nullopt,
          "tag_mapping_function"_a = nullopt,
          "new_ranges"_a = nullopt);
    m.def("as_formatted_evidence",
          &as_formatted_evidence<py::bytearray>,
          "text"_a,
          "text_ranges"_a = nullopt,
          "tag_mapping_function"_a = nullopt,
          "new_ranges"_a = nullopt);
    m.def("_convert_escaped_text_to_tainted_text",
          &_convert_escaped_text_to_taint_text<py::bytes>,
          "taint_escaped_text"_a,
          "ranges_orig"_a);
    m.def("_convert_escaped_text_to_tainted_text",
          &_convert_escaped_text_to_taint_text<py::str>,
          "taint_escaped_text"_a,
          "ranges_orig"_a);
    m.def("_convert_escaped_text_to_tainted_text",
          &_convert_escaped_text_to_taint_text_ba,
          "taint_escaped_text"_a,
          "ranges_orig"_a);
    m.def("parse_params", &parse_params);
}
