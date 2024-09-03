#include "AspectModulo.h"
#include "Helpers.h"

py::object
api_modulo_aspect_pyobject(py::object candidate_text, py::object candidate_tuple)
{
    return candidate_text.attr("__mod__")(candidate_tuple);
}

template<class StrType>
StrType
api_modulo_aspect(StrType candidate_text, py::object candidate_tuple)
{
    StrType result_o = candidate_text.attr("__mod__")(candidate_tuple);
    if (not is_text(candidate_text.ptr())) {
        return result_o;
    }

    py::tuple parameters =
      py::isinstance<py::tuple>(candidate_tuple) ? candidate_tuple : py::make_tuple(candidate_tuple);

    TRY_CATCH_ASPECT("modulo_aspect", , {
        auto [ranges_orig, candidate_text_ranges] = are_all_text_all_ranges(candidate_text.ptr(), parameters);

        if (ranges_orig.empty()) {
            return result_o;
        }

        // Note: PyCharm could mark an error on this call, but it's only because it doesn't correctly see
        // that at this point even if we entered from the py::object template instantiation, we are guaranteed
        // that the candidate_text is a StrType.
        StrType fmttext = as_formatted_evidence(candidate_text, candidate_text_ranges, TagMappingMode::Mapper);
        py::list list_formatted_parameters;

        for (const py::handle& param_handle : parameters) {
            auto param_strtype = py::reinterpret_borrow<py::object>(param_handle).cast<StrType>();
            if (is_text(param_handle.ptr())) {
                auto _ranges = api_get_ranges(param_handle);
                StrType n_parameter = as_formatted_evidence(param_strtype, _ranges, TagMappingMode::Mapper, nullopt);
                list_formatted_parameters.append(n_parameter);
            } else {
                list_formatted_parameters.append(param_handle);
            }
        }
        py::tuple formatted_parameters(list_formatted_parameters);

        py::str applied_params = fmttext.attr("__mod__")(formatted_parameters);
        return api_convert_escaped_text_to_taint_text(applied_params, ranges_orig);
    });
}

void
pyexport_aspect_modulo(py::module& m)
{
    m.def("_aspect_modulo",
          &api_modulo_aspect<py::str>,
          "candidate_text"_a,
          "candidate_tuple"_a,
          py::return_value_policy::move);
    m.def("_aspect_modulo",
          &api_modulo_aspect<py::bytes>,
          "candidate_text"_a,
          "candidate_tuple"_a,
          py::return_value_policy::move);
    m.def("_aspect_modulo",
          &api_modulo_aspect<py::bytearray>,
          "candidate_text"_a,
          "candidate_tuple"_a,
          py::return_value_policy::move);
    m.def("_aspect_modulo",
          &api_modulo_aspect_pyobject,
          "candidate_text"_a,
          "candidate_tuple"_a,
          py::return_value_policy::move);
}
