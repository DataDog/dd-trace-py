#include "Helpers.h"
#include <iostream> // JJJ: remove

using namespace pybind11::literals;
namespace py = pybind11;

size_t
get_pyobject_size(PyObject* obj)
{
    size_t len_candidate_text;
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


struct EVIDENCE_MARKS {
    static constexpr const char* BLANK = "";
    static constexpr const char* START_EVIDENCE = ":+-";
    static constexpr const char* END_EVIDENCE = "-+:";
    static constexpr const char* LESS = "<";
    static constexpr const char* GREATER = ">";
};

template <class StrType>
static StrType get_tag(const py::object& content) {
    if (content.is_none()) {
        return StrType(EVIDENCE_MARKS::BLANK);
    }

    if (py::isinstance<py::str>(StrType(EVIDENCE_MARKS::LESS))) {
        return StrType(EVIDENCE_MARKS::LESS) + content.cast<py::str>() + StrType(EVIDENCE_MARKS::GREATER);
    }
    return StrType(EVIDENCE_MARKS::LESS) + py::bytes(content.cast<py::str>()) + StrType(EVIDENCE_MARKS::GREATER);
}

// TODO OPTIMIZATION: check if we can use instead a struct object with range_guid_map, new_ranges and default members so we
// dont have to get the keys by string
static py::object mapper_replace(const TaintRangePtr& taint_range, const optional<const py::dict>& new_ranges) {
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

py::object get_default_content(const TaintRangePtr& taint_range) {
    if (!taint_range->source->name.empty()) {
        return py::str(taint_range->source->name);
    }

    return py::cast<py::none>(Py_None);
}

// TODO OPTIMIZATION: Remove py::types once this isn't used in Python
template <class StrType>
StrType as_formatted_evidence(const StrType& text,
                              optional<TaintRangeRefs>& text_ranges,
                              const optional<TagMappingMode>& tag_mapping_mode,
                              const optional<const py::dict>& new_ranges) {
    if (!text_ranges) {
        text_ranges = api_get_ranges(text);
    }
    if (text_ranges->empty()) {
        return text;
    }
    vector<StrType> res_vector;
    long index = 0;

    sort(text_ranges->begin(), text_ranges->end());
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

        res_vector.push_back(text[py::slice(py::int_{index}, py::int_{taint_range->start}, nullptr)]);
        res_vector.push_back(StrType(EVIDENCE_MARKS::START_EVIDENCE));
        res_vector.push_back(tag);
        res_vector.push_back(
                text[
                        py::slice(py::int_{taint_range->start}, py::int_{taint_range->start+taint_range->length},
                                  nullptr)]);
        res_vector.push_back(tag);
        res_vector.push_back(StrType(EVIDENCE_MARKS::END_EVIDENCE));

        index = taint_range->length;
    }
    res_vector.push_back(
            text[
                    py::slice(py::int_(index),
                              nullptr, nullptr)]);
    return StrType(EVIDENCE_MARKS::BLANK).attr("join")(res_vector);
}
void
pyexport_aspect_helpers(py::module& m)
{
    m.def("common_replace", &common_replace<py::bytes>, "string_method"_a, "candidate_text"_a);
    m.def("common_replace", &common_replace<py::str>, "string_method"_a, "candidate_text"_a);
    m.def("common_replace", &common_replace<py::bytearray>, "string_method"_a, "candidate_text"_a);
    m.def("as_formatted_evidence", &as_formatted_evidence<py::bytes>, "text"_a, "text_ranges"_a = nullopt,
          "tag_mapping_function"_a = nullopt, "new_ranges"_a = nullopt);
    m.def("as_formatted_evidence", &as_formatted_evidence<py::str>, "text"_a, "text_ranges"_a = nullopt,
          "tag_mapping_function"_a = nullopt, "new_ranges"_a = nullopt);
    m.def("as_formatted_evidence", &as_formatted_evidence<py::bytearray>, "text"_a, "text_ranges"_a = nullopt,
          "tag_mapping_function"_a = nullopt, "new_ranges"_a = nullopt);
}
