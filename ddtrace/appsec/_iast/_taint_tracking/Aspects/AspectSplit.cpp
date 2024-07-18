#include "AspectSplit.h"
#include "Initializer/Initializer.h"

template<class StrType>
py::list
api_split_text(const StrType& text, const optional<StrType>& separator, const optional<int> maxsplit)
{
    const auto split = text.attr("split");
    const auto split_result = split(separator, maxsplit);

    const auto tx_map = initializer->get_tainting_map();
    if (not tx_map or tx_map->empty()) {
        return split_result;
    }

    if (auto ranges = api_get_ranges(text); not ranges.empty()) {
        set_ranges_on_splitted(text, ranges, split_result, tx_map, false);
    }

    return split_result;
}

template<class StrType>
py::list
api_rsplit_text(const StrType& text, const optional<StrType>& separator, const optional<int> maxsplit)
{
    const auto rsplit = text.attr("rsplit");
    const auto split_result = rsplit(separator, maxsplit);
    const auto tx_map = initializer->get_tainting_map();
    if (not tx_map or tx_map->empty()) {
        return split_result;
    }

    if (auto ranges = api_get_ranges(text); not ranges.empty()) {
        set_ranges_on_splitted(text, ranges, split_result, tx_map, false);
    }
    return split_result;
}

template<class StrType>
py::list
api_splitlines_text(const StrType& text, bool keepends)
{
    const auto splitlines = text.attr("splitlines");
    const auto split_result = splitlines(keepends);
    const auto tx_map = initializer->get_tainting_map();
    if (not tx_map or tx_map->empty()) {
        return split_result;
    }

    if (auto ranges = api_get_ranges(text); not ranges.empty()) {
        set_ranges_on_splitted(text, ranges, split_result, tx_map, keepends);
    }
    return split_result;
}

void
pyexport_aspect_split(py::module& m)
{
    m.def("_aspect_split",
          &api_split_text<py::str>,
          "text"_a,
          "separator"_a = py::none(),
          "maxsplit"_a = -1,
          py::return_value_policy::move);
    m.def("_aspect_split",
          &api_split_text<py::bytes>,
          "text"_a,
          "separator"_a = py::none(),
          "maxsplit"_a = -1,
          py::return_value_policy::move);
    m.def("_aspect_split",
          &api_split_text<py::bytearray>,
          "text"_a,
          "separator"_a = py::none(),
          "maxsplit"_a = -1,
          py::return_value_policy::move);
    m.def("_aspect_rsplit",
          &api_rsplit_text<py::str>,
          "text"_a,
          "separator"_a = py::none(),
          "maxsplit"_a = -1,
          py::return_value_policy::move);
    m.def("_aspect_rsplit",
          &api_rsplit_text<py::bytes>,
          "text"_a,
          "separator"_a = py::none(),
          "maxsplit"_a = -1,
          py::return_value_policy::move);
    m.def("_aspect_rsplit",
          &api_rsplit_text<py::bytearray>,
          "text"_a,
          "separator"_a = py::none(),
          "maxsplit"_a = -1,
          py::return_value_policy::move);
    // cppcheck-suppress assignBoolToPointer
    m.def("_aspect_splitlines",
          &api_splitlines_text<py::str>,
          "text"_a,
          "keepends"_a = false,
          py::return_value_policy::move);
    // cppcheck-suppress assignBoolToPointer
    m.def("_aspect_splitlines",
          &api_splitlines_text<py::bytes>,
          "text"_a,
          "keepends"_a = false,
          py::return_value_policy::move);
    // cppcheck-suppress assignBoolToPointer
    m.def("_aspect_splitlines",
          &api_splitlines_text<py::bytearray>,
          "text"_a,
          "keepends"_a = false,
          py::return_value_policy::move);
}