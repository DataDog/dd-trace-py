#include "AspectOsPathJoin.h"
#include <string>
#include <iostream>  // JJJ

template<class StrType>
StrType
api_ospathjoin_aspect(StrType& first_part,
                  const py::args& args)
{
    auto ospath = py::module_::import("os.path");
    auto join = ospath.attr("join");
    auto joined = join(first_part, *args);

    auto tx_map = initializer->get_tainting_map();
    if (not tx_map or tx_map->empty()) {
        return joined;
    }

    std::string separator = py::str(ospath.attr("sep")).cast<std::string>();
    auto sepsize = separator.size();

    // Find the initial iteration point. This will be the first argument that has the separator ("/foo")
    // as a first character or first_part (the first element) if no such argument is found.
    long unsigned initial_arg_pos = 0;
    bool root_is_after_first = false;
    for (auto &arg : args) {
        if (not is_text(arg.ptr())) {
            return joined;
        }
        std::string carg = py::cast<std::string>(arg);
        if (carg.substr(0, 1) == separator) {
            root_is_after_first = true;
            initial_arg_pos++;
            break;
        }
        initial_arg_pos++;
    }

    if (not root_is_after_first) {
        initial_arg_pos = 0;
    }


    /*
    // Put first_part and all the args into a vector, converting them to std::string
    std::vector<py::object> all_args;
    std::vector<TaintRangeRefs> all_ranges;

    // Add the valid arguments and potential ranges to two vectors
    if (not root_is_after_first) {
        all_args.emplace_back(py::cast<py::object>(first_part));
        all_ranges.emplace_back(api_get_ranges(first_part));
    }

    for (long unsigned i = initial_arg_pos; i < args.size(); i++) {
        auto pyobject_arg = py::cast<py::object>(args[i]);
        all_args.emplace_back(pyobject_arg);
        all_ranges.emplace_back(api_get_ranges(pyobject_arg));
    }

    int current_offset;
    bool first_had_sep = false;
    // If initial_arg starts with the separator ("/foo"), skip it for the string offset
    if (all_args[initial_arg_pos].cast<std::string>().substr(0, 1) == separator) {
        current_offset = sepsize;
        first_had_sep = true;
    } else {
        current_offset = 0;
    }

    // Iterate over all_args from initial_arg_pos to the end, updating the current_offset
    // to the position of the last character of the current argument and the separator
    TaintRangeRefs result_ranges;

    for (long unsigned i = initial_arg_pos; i < all_args.size(); i++) {
        auto ranges = all_ranges[i];
        if (not ranges.empty()) {
            for (auto &range : ranges) {
                result_ranges.emplace_back(api_shift_taint_range(range, current_offset, py::len(all_args[i])));
            }
        }

        if (i == initial_arg_pos and first_had_sep) {
            current_offset += py::len(all_args[i]) - sepsize;
        } else {
            current_offset += py::len(all_args[i]);
        }

        current_offset += sepsize;
    }
    current_offset -= sepsize;

    if (not result_ranges.empty()) {
        PyObject* new_result = new_pyobject_id(joined.ptr());
        set_ranges(new_result, result_ranges, tx_map);
        return py::reinterpret_steal<StrType>(new_result);
    }

     */

    return joined;
}

void
pyexport_ospathjoin_aspect(py::module& m)
{
    m.def("_aspect_ospathjoin", &api_ospathjoin_aspect<py::str>, "first_part"_a);
    m.def("_aspect_ospathjoin", &api_ospathjoin_aspect<py::bytes>, "first_part"_a);
}
