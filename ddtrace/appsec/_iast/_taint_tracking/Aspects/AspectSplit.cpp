#include "AspectSplit.h"
#include "Initializer/Initializer.h"
#include "TaintedOps/TaintedOps.h"
#include <iostream>  // JJJ


static std::pair<py::object, py::object> get_separator_maxsplit_from_args(const py::tuple& args, const py::kwargs& kwargs)
{
    cerr << "JJJ getsep1 \n";
    py::object separator;
    if (kwargs.contains("separator")) {
    cerr << "JJJ getsep2 \n";
        separator = kwargs["separator"];
    } else {
    cerr << "JJJ getsep3 \n";
        if (args.size() > 1) {
    cerr << "JJJ getsep4 \n";
            separator = args[1];
        } else {
    cerr << "JJJ getsep5 \n";
            separator = py::none();
        }
    cerr << "JJJ getsep6 \n";
    }

    auto max_split = kwargs.contains("maxsplit") ? kwargs["maxsplit"] : args.size() > 2 ? args[2] : py::int_(-1);
    cerr << "JJJ getsep7 \n";
    return std::make_pair(separator, max_split);
}

static std::optional<py::list> handle_potential_re_split(const py::tuple& args, const py::kwargs& kwargs, const TaintRangeMapTypePtr& tx_map)
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
        const py::tuple sliced_args = args[py::slice(1, len(args), 1)];
        py::list result = split_func(*sliced_args, **kwargs);

        if (const int offset = isinstance(args[0], re_pattern_type) ? -1 : 0;
            args.size() >= (static_cast<size_t>(3) + offset) && is_tainted(args[2 + offset].ptr(), tx_map)) {
            for (auto& i : result) {
                if (!i.is_none()) {
                    copy_and_shift_ranges_from_strings(args[2 + offset], i, 0, len(i), tx_map);
                }
            }
        }
        return result;
    }

    return std::nullopt;
}

template<class StrType>
static py::list
split_text_common(const py::object& orig_function,
                 const int flag_added_args,
                 const py::args& args,
                 const py::kwargs& kwargs,
                 const std::string& split_func)
{
    cerr << "JJJ 1\n";
    PyObject* result_or_args = process_flag_added_args(orig_function.ptr(), flag_added_args, args.ptr(), kwargs.ptr());
    py::tuple args_tuple;
    if (PyTuple_Check(result_or_args)) {
    cerr << "JJJ 1.1\n";
        args_tuple = py::reinterpret_borrow<py::tuple>(result_or_args);
    } else {
    cerr << "JJJ 1.2\n";
        return py::reinterpret_borrow<py::list>(result_or_args);
    }

    cerr << "JJJ 1.5\n";
    const auto& text = args_tuple[0];

    cerr << "JJJ 2\n";
    auto [separator, max_split] = get_separator_maxsplit_from_args(args_tuple, kwargs);
    cerr << "JJJ 2.5 separator: " << py::str(separator) << ", max_split: " << py::str(max_split) << "\n";
    py::object result_o = text.attr(split_func.c_str())(separator, max_split);
    cerr << "JJJ 3, result_o: " << py::str(result_o) << "\n";

    TRY_CATCH_ASPECT("split_aspect", {
        if (split_func == "split") {
            if (auto re_split_result = handle_potential_re_split(args_tuple, kwargs, Initializer::get_tainting_map());re_split_result.has_value()) {
    cerr << "JJJ 4\n";
                return *re_split_result;
            }
        }

        const auto tx_map = Initializer::get_tainting_map();
        if (!tx_map || tx_map->empty()) {
    cerr << "JJJ 5\n";
            return result_o;
        }


        if (auto ranges = api_get_ranges(text); !ranges.empty()) {
            set_ranges_on_splitted(text, ranges, result_o, tx_map, false);
        }

    cerr << "JJJ 6\n";
        return result_o;
    });
}


template<class StrType>
py::list
api_splitlines_text(const py::object& orig_function,
                 const int flag_added_args,
                 const py::args& args,
                 const py::kwargs& kwargs)
{
    const py::object result_or_args = process_flag_added_args(orig_function, flag_added_args, args, kwargs);

    py::tuple args_tuple;
    if (py::isinstance<py::tuple>(result_or_args)) {
        args_tuple = result_or_args.cast<py::tuple>();
    } else {
        return result_or_args.cast<py::list>();
    }

    const auto& text = args_tuple[0];
    bool keepends = false;
    if (kwargs.contains("keepends")) {
        keepends = kwargs["keepends"].cast<bool>();
    } else {
        if (args.size() > 1) {
            keepends = args[1].cast<bool>();
        }
    }
    py::object result_o = text.attr("splitlines")(keepends);

    TRY_CATCH_ASPECT("splitlines_aspect", {
        const auto tx_map = Initializer::get_tainting_map();
        if (!tx_map || tx_map->empty()) {
            return result_o;
        }

        auto [ranges, ranges_error] = get_ranges(text.ptr(), tx_map);
        if (ranges_error || ranges.empty()) {
            return result_o;
        }
        set_ranges_on_splitted(text, ranges, result_o, tx_map, keepends);
        return result_o;
    });
}

void
pyexport_aspect_split(py::module& m)
{
    m.def("_aspect_split",
      [](const py::object& orig_function, const int flag_added_args, py::args args, const py::kwargs& kwargs) {
          return split_text_common<py::str>(orig_function, flag_added_args, args, kwargs, "split");
      },
      "orig_function"_a = py::none(),
      "flag_added_args"_a = 0,
      py::return_value_policy::move);

    m.def("_aspect_split",
      [](const py::object& orig_function, const int flag_added_args, py::args args, const py::kwargs& kwargs) {
          return split_text_common<py::bytes>(orig_function, flag_added_args, args, kwargs, "split");
      },
      "orig_function"_a = py::none(),
      "flag_added_args"_a = 0,
      py::return_value_policy::move);

    m.def("_aspect_split",
      [](const py::object& orig_function, const int flag_added_args, py::args args, const py::kwargs& kwargs) {
          return split_text_common<py::bytearray>(orig_function, flag_added_args, args, kwargs, "split");
      },
      "orig_function"_a = py::none(),
      "flag_added_args"_a = 0,
      py::return_value_policy::move);

    m.def("_aspect_rsplit",
      [](const py::object& orig_function, const int flag_added_args, py::args args, const py::kwargs& kwargs) {
          return split_text_common<py::str>(orig_function, flag_added_args, args, kwargs, "rsplit");
      },
      "orig_function"_a = py::none(),
      "flag_added_args"_a = 0,
      py::return_value_policy::move);

    m.def("_aspect_rsplit",
      [](const py::object& orig_function, const int flag_added_args, py::args args, const py::kwargs& kwargs) {
          return split_text_common<py::bytes>(orig_function, flag_added_args, args, kwargs, "rsplit");
      },
      "orig_function"_a = py::none(),
      "flag_added_args"_a = 0,
      py::return_value_policy::move);

    m.def("_aspect_rsplit",
      [](const py::object& orig_function, const int flag_added_args, py::args args, const py::kwargs& kwargs) {
          return split_text_common<py::bytearray>(orig_function, flag_added_args, args, kwargs, "rsplit");
      },
      "orig_function"_a = py::none(),
      "flag_added_args"_a = 0,
      py::return_value_policy::move);

    m.def("_aspect_splitlines",
        &api_splitlines_text<py::str>,
      "orig_function"_a = py::none(),
      "flag_added_args"_a = 0,
      py::return_value_policy::move);
}