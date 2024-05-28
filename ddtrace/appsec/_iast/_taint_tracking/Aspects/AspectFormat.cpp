#include "Aspects/AspectFormat.h"

/**
 * @brief This function is used to format the candidate_text with the given parameter_list, args and kwargs.
 *
 * @tparam StrType
 * @param candidate_text
 * @param parameter_list
 * @param args
 * @param kwargs
 * @return StrType
 */
template<class StrType>
StrType
api_format_aspect(StrType& candidate_text,
                  const py::tuple& parameter_list,
                  const py::args& args,
                  const py::kwargs& kwargs)
{
    const auto tx_map = initializer->get_tainting_map();

    if (not tx_map or tx_map->empty()) {
        return py::getattr(candidate_text, "format")(*args, **kwargs);
    }

    auto [ranges_orig, candidate_text_ranges] = are_all_text_all_ranges(candidate_text.ptr(), parameter_list);

    if (!ranges_orig.empty() or !candidate_text_ranges.empty()) {
        auto new_template =
          int_as_formatted_evidence<StrType>(candidate_text, candidate_text_ranges, TagMappingMode::Mapper);

        py::list new_args;
        py::dict new_kwargs;
        for (const auto arg : args) {
            if (is_text(arg.ptr())) {
                auto str_arg = py::cast<py::str>(arg);
                auto n_arg = all_as_formatted_evidence<py::str>(str_arg, TagMappingMode::Mapper);
                new_args.append(n_arg);
            } else {
                new_args.append(arg);
            }
        }
        for (auto [key, value] : kwargs) {
            if (is_text(value.ptr())) {
                auto str_value = py::cast<py::str>(value);
                auto n_value = all_as_formatted_evidence<py::str>(str_value, TagMappingMode::Mapper);
                new_kwargs[key] = n_value;
            } else {
                new_kwargs[key] = value;
            }
        }
        StrType new_template_format =
          py::getattr(new_template, "format")(*(py::cast<py::tuple>(new_args)), **new_kwargs);
        std::tuple result = convert_escaped_text_to_taint_text<StrType>(new_template_format, ranges_orig);
        StrType result_text = get<0>(result);
        TaintRangeRefs result_ranges = get<1>(result);
        PyObject* new_result = new_pyobject_id(result_text.ptr());
        set_ranges(new_result, result_ranges, tx_map);
        return py::reinterpret_steal<StrType>(new_result);
    }
    return py::getattr(candidate_text, "format")(*args, **kwargs);
}

void
pyexport_format_aspect(py::module& m)
{
    m.def("_format_aspect", &api_format_aspect<py::str>, "candidate_text"_a, "parameter_list"_a);
}