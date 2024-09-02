#include "AspectSplit.h"
#include "Initializer/Initializer.h"
#include "TaintedOps/TaintedOps.h"

static std::optional<py::object>
handle_potential_re_split(const py::tuple& args,
                          const py::tuple& sliced_args,
                          const py::kwargs& kwargs,
                          const TaintRangeMapTypePtr& tx_map)
{
    const py::module re = py::module::import("re");
    const py::object re_pattern_type = re.attr("Pattern");
    const py::object types_module = py::module::import("types");

    // re.split aspect, either with pattern as first arg or with re module
    if (const py::object module_type = types_module.attr("ModuleType");
        isinstance(args[0], re_pattern_type) ||
        (isinstance(args[0], module_type) && std::string(py::str(args[0].attr("__name__"))) == "re" &&
         (std::string(py::str(args[0].attr("__package__"))).empty() ||
          std::string(py::str(args[0].attr("__package__"))) == "re"))) {

        const py::object split_func = args[0].attr("split");
        // Create a py::slice object to slice the args from index 1 to the end
        py::list result = split_func(*sliced_args, **kwargs);

        if (const int offset = isinstance(args[0], re_pattern_type) ? -1 : 0;
            args.size() >= (static_cast<size_t>(3) + offset) && is_tainted(args[2 + offset].ptr(), tx_map)) {
            for (auto& i : result) {
                if (!i.is_none() and len(i) > 0) {
                    copy_and_shift_ranges_from_strings(args[2 + offset], i, 0, len(i), tx_map);
                }
            }
        }
        return result;
    }

    return std::nullopt;
}

static py::object
split_text_common(const py::object& orig_function,
                  const int flag_added_args,
                  const py::args& args,
                  const py::kwargs& kwargs,
                  const std::string& split_func)
{
    PyObject* result_or_args = process_flag_added_args(orig_function.ptr(), flag_added_args, args.ptr(), kwargs.ptr());
    py::tuple args_tuple;
    if (PyTuple_Check(result_or_args)) {
        args_tuple = py::reinterpret_borrow<py::tuple>(result_or_args);
    } else {
        return py::reinterpret_borrow<py::list>(result_or_args);
    }

    const auto& text = args_tuple[0];

    const py::tuple sliced_args = len(args) > 1 ? args[py::slice(1, len(args), 1)] : py::tuple(); // (,)
    auto result_o = text.attr(split_func.c_str())(*sliced_args, **kwargs); // returns['', ''] WHY?

    const auto tx_map = Initializer::get_tainting_map();
    if (!tx_map || tx_map->empty()) {
        return result_o;
    }

    TRY_CATCH_ASPECT("split_aspect", , {
        if (split_func == "split") {
            if (auto re_split_result = handle_potential_re_split(args_tuple, sliced_args, kwargs, tx_map);
                re_split_result.has_value()) {
                return *re_split_result;
            }
        }

        auto [ranges, ranges_error] = get_ranges(text.ptr(), tx_map);
        if (!ranges_error and !ranges.empty()) {
            set_ranges_on_splitted(text, ranges, result_o, tx_map, false);
        }
    });
    return result_o;
}

py::object
api_splitlines_text(const py::object& orig_function,
                    const int flag_added_args,
                    const py::args& args,
                    const py::kwargs& kwargs)
{
    const auto result_or_args = py::reinterpret_borrow<py::object>(
      process_flag_added_args(orig_function.ptr(), flag_added_args, args.ptr(), kwargs.ptr()));

    py::tuple args_tuple;
    if (py::isinstance<py::tuple>(result_or_args)) {
        args_tuple = result_or_args.cast<py::tuple>();
    } else {
        return result_or_args.cast<py::list>();
    }

    const auto& text = args_tuple[0];
    const py::tuple sliced_args = len(args) > 1 ? args[py::slice(1, len(args), 1)] : py::tuple();
    py::object result_o = text.attr("splitlines")(*sliced_args, **kwargs);

    const auto tx_map = Initializer::get_tainting_map();
    if (!tx_map || tx_map->empty()) {
        return result_o;
    }

    TRY_CATCH_ASPECT("split_aspect", , {
        auto [ranges, ranges_error] = get_ranges(text.ptr(), tx_map);
        if (ranges_error || ranges.empty()) {
            return result_o;
        }

        // Retrieve keepends and check that is a boolean. If not, return the original value
        // because it could be a method of a different type.
        bool keepends_is_other_type = false;
        bool keepends = false;
        if (kwargs.contains("keepends")) {
            if (py::isinstance<py::bool_>(kwargs["keepends"])) {
                keepends = kwargs["keepends"].cast<bool>();
            } else {
                keepends_is_other_type = true;
            }
        } else {
            if (args.size() > 1) {
                if (py::isinstance<py::bool_>(args[1])) {
                    keepends = args[1].cast<bool>();
                } else {
                    keepends_is_other_type = true;
                }
            }
        }

        if (!keepends_is_other_type) {
            set_ranges_on_splitted(text, ranges, result_o, tx_map, keepends);
        }
    });
    return result_o;
}

void
pyexport_aspect_split(py::module& m)
{
    m.def(
      "_aspect_split",
      [](const py::object& orig_function, const int flag_added_args, const py::args& args, const py::kwargs& kwargs) {
          return split_text_common(orig_function, flag_added_args, args, kwargs, "split");
      },
      "orig_function"_a = py::none(),
      "flag_added_args"_a = 0,
      py::return_value_policy::move);

    m.def(
      "_aspect_rsplit",
      [](const py::object& orig_function, const int flag_added_args, const py::args& args, const py::kwargs& kwargs) {
          return split_text_common(orig_function, flag_added_args, args, kwargs, "rsplit");
      },
      "orig_function"_a = py::none(),
      "flag_added_args"_a = 0,
      py::return_value_policy::move);

    m.def("_aspect_splitlines",
          &api_splitlines_text,
          "orig_function"_a = py::none(),
          "flag_added_args"_a = 0,
          py::return_value_policy::move);
}
