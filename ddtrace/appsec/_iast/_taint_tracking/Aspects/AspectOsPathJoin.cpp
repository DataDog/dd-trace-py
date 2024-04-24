#include "AspectOsPathJoin.h"
#include <string>

static bool
starts_with_separator(const py::handle& arg, const std::string& separator)
{
    std::string carg = py::cast<std::string>(arg);
    return carg.substr(0, 1) == separator;
}

template<class StrType>
StrType
api_ospathjoin_aspect(StrType& first_part, const py::args& args)
{
    auto ospath = py::module_::import("os.path");
    auto join = ospath.attr("join");
    auto joined = join(first_part, *args);

    auto tx_map = initializer->get_tainting_map();
    if (not tx_map or tx_map->empty()) {
        return joined;
    }

    std::string separator = ospath.attr("sep").cast<std::string>();
    auto sepsize = separator.size();

    // Find the initial iteration point. This will be the first argument that has the separator ("/foo")
    // as a first character or first_part (the first element) if no such argument is found.
    auto initial_arg_pos = -1;
    bool root_is_after_first = false;
    for (auto& arg : args) {
        if (not is_text(arg.ptr())) {
            return joined;
        }

        if (starts_with_separator(arg, separator)) {
            root_is_after_first = true;
            initial_arg_pos++;
            break;
        }
        initial_arg_pos++;
    }

    TaintRangeRefs result_ranges;
    result_ranges.reserve(args.size());

    std::vector<TaintRangeRefs> all_ranges;
    unsigned long current_offset = 0;
    auto first_part_len = py::len(first_part);

    if (not root_is_after_first) {
        // Get the ranges of first_part and set them to the result, skipping the first character position
        // if it's a separator
        auto ranges = api_get_ranges(first_part);
        if (not ranges.empty()) {
            for (auto& range : ranges) {
                result_ranges.emplace_back(api_shift_taint_range(range, current_offset, first_part_len));
            }
        }

        if (not first_part.is(py::str(separator))) {
            current_offset = py::len(first_part);
        }

        current_offset += sepsize;
        initial_arg_pos = 0;
    }

    unsigned long unsigned_initial_arg_pos = max(0, initial_arg_pos);

    // Now go trough the arguments and do the same
    for (unsigned long i = 0; i < args.size(); i++) {
        if (i >= unsigned_initial_arg_pos) {
            // Set the ranges from the corresponding argument
            auto ranges = api_get_ranges(args[i]);
            if (not ranges.empty()) {
                auto len_args_i = py::len(args[i]);
                for (auto& range : ranges) {
                    result_ranges.emplace_back(api_shift_taint_range(range, current_offset, len_args_i));
                }
            }
            current_offset += py::len(args[i]);
            current_offset += sepsize;
        }
    }

    if (not result_ranges.empty()) {
        PyObject* new_result = new_pyobject_id(joined.ptr());
        set_ranges(new_result, result_ranges, tx_map);
        return py::reinterpret_steal<StrType>(new_result);
    }

    return joined;
}

void
pyexport_ospathjoin_aspect(py::module& m)
{
    m.def("_aspect_ospathjoin", &api_ospathjoin_aspect<py::str>, "first_part"_a);
    m.def("_aspect_ospathjoin", &api_ospathjoin_aspect<py::bytes>, "first_part"_a);
}
