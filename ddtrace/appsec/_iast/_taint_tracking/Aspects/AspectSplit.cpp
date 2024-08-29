#include "AspectSplit.h"
#include "Initializer/Initializer.h"
#include "TaintedOps/TaintedOps.h"

template<class StrType>
py::list
api_split_text(const py::object& orig_function,
                 const int flag_added_args,
                 const py::args& args,
                 const py::kwargs& kwargs)
{
    const py::object result_or_args = process_args(orig_function, flag_added_args, args, kwargs);

    py::tuple args_tuple;
    if (py::isinstance<py::tuple>(result_or_args)) {
        args_tuple = result_or_args.cast<py::tuple>();
    } else {
        return result_or_args.cast<py::list>();
    }


    const auto& text = py::cast<StrType>(args_tuple[0]);
    // const py::object separator = kwargs.contains("separator") ? kwargs["separator"].cast<StrType>() : args_tuple.size() > 1 ? args_tuple[1] : py::none();
    py::object separator;

    if (kwargs.contains("separator")) {
        separator = kwargs["separator"];
    } else {
        if (args_tuple.size() > 1) {
            separator = args_tuple[1];
        } else {
            separator = py::none();
        }
    }
    const int maxsplit = kwargs.contains("maxsplit") ? kwargs["maxsplit"].cast<int>() : args_tuple.size() > 2 ? py::cast<int>(args_tuple[2]) : -1;
    py::object split = text.attr("split");
    py::object result_o = split(separator, maxsplit);

    TRY_CATCH_ASPECT("split_aspect", {
        const auto tx_map = Initializer::get_tainting_map();
        if (!tx_map || tx_map->empty()) {
            return result_o;
        }

        const py::module re = py::module::import("re");
        const py::object re_pattern_type = re.attr("Pattern");
        const py::object types_module = py::module::import("types");

        // re.split aspect, either with pattern as first arg or with re module
        if (const py::object module_type = types_module.attr("ModuleType");
            isinstance(args_tuple[0], re_pattern_type) ||
            (isinstance(args_tuple[0], module_type) && std::string(py::str(args_tuple[0].attr("__name__"))) == "re" &&
             (std::string(py::str(args_tuple[0].attr("__package__"))).empty() ||
              std::string(py::str(args_tuple[0].attr("__package__"))) == "re"))) {

            const py::object split_func = args_tuple[0].attr("split");
            // Create a py::slice object to slice the args_tuple from index 1 to the end
            const py::tuple sliced_args = args_tuple[py::slice(1, len(args_tuple), 1)];
            const py::list result = split_func(*sliced_args, **kwargs);

            if (const int offset = isinstance(args_tuple[0], re_pattern_type) ? -1 : 0;
                args_tuple.size() >= (static_cast<size_t>(3) + offset) && is_tainted(args_tuple[2 + offset].ptr(), tx_map)) {
                for (auto i : result) {
                    if (!i.is_none()) {
                        copy_and_shift_ranges_from_strings(args_tuple[2 + offset], i, 0, len(i), tx_map);
                    }
                }
            }
            return result;
        }

        if (auto ranges = api_get_ranges(text); !ranges.empty()) {
            set_ranges_on_splitted(text, ranges, result_o, tx_map, false);
        }

        return result_o;
    });
}

template<class StrType>
py::list
api_rsplit_text(const StrType& text, const optional<StrType>& separator, const optional<int> maxsplit)
{
    const auto rsplit = text.attr("rsplit");
    const auto split_result = rsplit(separator, maxsplit);
    const auto tx_map = Initializer::get_tainting_map();
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
    const auto tx_map = Initializer::get_tainting_map();
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
      "orig_function"_a = py::none(),
      "flag_added_args"_a = 0,
      py::return_value_policy::move);
    m.def("_aspect_split",
      &api_split_text<py::bytes>,
      "orig_function"_a = py::none(),
      "flag_added_args"_a = 0,
      py::return_value_policy::move);
    m.def("_aspect_split",
      &api_split_text<py::bytearray>,
      "orig_function"_a = py::none(),
      "flag_added_args"_a = 0,
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