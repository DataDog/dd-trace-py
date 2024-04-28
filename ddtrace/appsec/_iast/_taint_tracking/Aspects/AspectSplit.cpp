#include "AspectSplit.h"
#include "Initializer/Initializer.h"

template<class StrType>
py::list
api_split_text(const StrType& text, const optional<StrType>& separator, const optional<int> maxsplit)
{
    TaintRangeMapType* tx_map = initializer->get_tainting_map();
    if (not tx_map) {
        throw py::value_error(MSG_ERROR_TAINT_MAP);
    }

    auto split = text.attr("split");
    auto split_result = split(separator, maxsplit);
    auto ranges = api_get_ranges(text);
    if (not ranges.empty()) {
        set_ranges_on_splitted(text, ranges, split_result, tx_map, false);
    }

    return split_result;
}

template<class StrType>
py::list
api_rsplit_text(const StrType& text, const optional<StrType>& separator, const optional<int> maxsplit)
{
    TaintRangeMapType* tx_map = initializer->get_tainting_map();
    if (not tx_map) {
        throw py::value_error(MSG_ERROR_TAINT_MAP);
    }

    auto rsplit = text.attr("rsplit");
    auto split_result = rsplit(separator, maxsplit);
    auto ranges = api_get_ranges(text);
    if (not ranges.empty()) {
        set_ranges_on_splitted(text, ranges, split_result, tx_map, false);
    }
    return split_result;
}

template<class StrType>
py::list
api_splitlines_text(const StrType& text, bool keepends)
{
    TaintRangeMapType* tx_map = initializer->get_tainting_map();
    if (not tx_map) {
        throw py::value_error(MSG_ERROR_TAINT_MAP);
    }

    auto splitlines = text.attr("splitlines");
    auto split_result = splitlines(keepends);
    auto ranges = api_get_ranges(text);
    if (not ranges.empty()) {
        set_ranges_on_splitted(text, ranges, split_result, tx_map, keepends);
    }
    return split_result;
}

void
pyexport_aspect_split(py::module& m)
{
    m.def("_aspect_split", &api_split_text<py::str>, "text"_a, "separator"_a = py::none(), "maxsplit"_a = -1);
    m.def("_aspect_split", &api_split_text<py::bytes>, "text"_a, "separator"_a = py::none(), "maxsplit"_a = -1);
    m.def("_aspect_split", &api_split_text<py::bytearray>, "text"_a, "separator"_a = py::none(), "maxsplit"_a = -1);
    m.def("_aspect_rsplit", &api_rsplit_text<py::str>, "text"_a, "separator"_a = py::none(), "maxsplit"_a = -1);
    m.def("_aspect_rsplit", &api_rsplit_text<py::bytes>, "text"_a, "separator"_a = py::none(), "maxsplit"_a = -1);
    m.def("_aspect_rsplit", &api_rsplit_text<py::bytearray>, "text"_a, "separator"_a = py::none(), "maxsplit"_a = -1);
    // cppcheck-suppress assignBoolToPointer
    m.def("_aspect_splitlines", &api_splitlines_text<py::str>, "text"_a, "keepends"_a = false);
    // cppcheck-suppress assignBoolToPointer
    m.def("_aspect_splitlines", &api_splitlines_text<py::bytes>, "text"_a, "keepends"_a = false);
    // cppcheck-suppress assignBoolToPointer
    m.def("_aspect_splitlines", &api_splitlines_text<py::bytearray>, "text"_a, "keepends"_a = false);
}