#include "AspectLower.h"
#include "Initializer/Initializer.h"
#include "TaintedOps/TaintedOps.h"

static py::object
api_lower_text(const py::object& orig_function,
                  const int flag_added_args,
                  const py::args& args,
                  const py::kwargs& kwargs)
{
    PyObject* result_or_args = process_flag_added_args(orig_function.ptr(), flag_added_args, args.ptr(), kwargs.ptr());
    py::tuple args_tuple;
    if (PyTuple_Check(result_or_args)) {
        args_tuple = py::reinterpret_borrow<py::tuple>(result_or_args);
    } else {
        return py::reinterpret_borrow<py::list>(result_or_args);
    }

    const auto& text = args_tuple[0];
    string lower_func = "lower";

    const py::tuple sliced_args = len(args) > 1 ? args[py::slice(1, len(args), 1)] : py::tuple(); // (,)
    auto result_o = text.attr(lower_func.c_str())(*sliced_args, **kwargs);

    const auto tx_map = Initializer::get_tainting_map();
    if (!tx_map || tx_map->empty()) {
        return result_o;
    }

    TRY_CATCH_ASPECT("lower_aspect", return result_o, , {
        auto [ranges, ranges_error] = get_ranges(text.ptr(), tx_map);
        if (!ranges_error and !ranges.empty()) {
            set_ranges(result_o.ptr(), ranges, tx_map);
        }
    });
    return result_o;
}

void pyexport_aspect_lower(py::module& m){
    m.def(
        "aspect_lower",
        [](const py::object& orig_function, const int flag_added_args, const py::args& args, const py::kwargs& kwargs) {
            return api_lower_text(orig_function, flag_added_args, args, kwargs);
        },
        "orig_function"_a = py::none(),
        "flag_added_args"_a = 0,
        py::return_value_policy::move);
}


