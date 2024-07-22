#include "AspectsOsPath.h"
#include <string>

#include "Helpers.h"

static bool
starts_with_separator(const py::handle& arg, const std::string& separator)
{
    const auto carg = py::cast<std::string>(arg);
    return carg.substr(0, 1) == separator;
}

template<class StrType>
StrType
api_ospathjoin_aspect(StrType& first_part, const py::args& args)
{
    const auto ospath = py::module_::import("os.path");
    auto join = ospath.attr("join");
    auto joined = join(first_part, *args);

    const auto tx_map = initializer->get_tainting_map();
    if (not tx_map or tx_map->empty()) {
        return joined;
    }

    const auto separator = ospath.attr("sep").cast<std::string>();
    const auto sepsize = separator.size();

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
        auto [ranges, ranges_error] = get_ranges(first_part.ptr(), tx_map);
        if (not ranges_error and not ranges.empty()) {
            for (auto& range : ranges) {
                result_ranges.emplace_back(shift_taint_range(range, current_offset, first_part_len));
            }
        }

        if (not first_part.is(py::str(separator))) {
            current_offset = py::len(first_part);
        }

        current_offset += sepsize;
        initial_arg_pos = 0;
    }

    const unsigned long unsigned_initial_arg_pos = max(0, initial_arg_pos);

    // Now go trough the arguments and do the same
    for (unsigned long i = 0; i < args.size(); i++) {
        if (i >= unsigned_initial_arg_pos) {
            // Set the ranges from the corresponding argument
            if (auto [ranges, ranges_error] = get_ranges(args[i].ptr(), tx_map);
                not ranges_error and not ranges.empty()) {
                const auto len_args_i = py::len(args[i]);
                for (auto& range : ranges) {
                    result_ranges.emplace_back(shift_taint_range(range, current_offset, len_args_i));
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

template<class StrType>
StrType
api_ospathbasename_aspect(const StrType& path)
{
    const auto ospath = py::module_::import("os.path");
    auto basename = ospath.attr("basename");
    auto basename_result = basename(path);

    const auto tx_map = initializer->get_tainting_map();
    if (not tx_map or tx_map->empty() or py::len(basename_result) == 0) {
        return basename_result;
    }

    auto [ranges, ranges_error] = get_ranges(path.ptr(), tx_map);
    if (ranges_error or ranges.empty()) {
        return basename_result;
    }

    // Create a fake list to call set_ranges_on_splitted on it (we are
    // only interested on the last path, which is the basename result)
    auto prev_path_len = py::len(path) - py::len(basename_result);
    std::string filler(prev_path_len, 'X');
    py::str filler_str(filler);
    py::list apply_list;
    apply_list.append(filler_str);
    apply_list.append(basename_result);

    set_ranges_on_splitted(path, ranges, apply_list, tx_map, false);
    return apply_list[1];
}

template<class StrType>
StrType
api_ospathdirname_aspect(const StrType& path)
{
    const auto ospath = py::module_::import("os.path");
    auto dirname = ospath.attr("dirname");
    auto dirname_result = dirname(path);

    const auto tx_map = initializer->get_tainting_map();
    if (not tx_map or tx_map->empty() or py::len(dirname_result) == 0) {
        return dirname_result;
    }

    auto [ranges, ranges_error] = get_ranges(path.ptr(), tx_map);
    if (ranges_error or ranges.empty()) {
        return dirname_result;
    }

    // Create a fake list to call set_ranges_on_splitted on it (we are
    // only interested on the first path, which is the dirname result)
    auto prev_path_len = py::len(path) - py::len(dirname_result);
    std::string filler(prev_path_len, 'X');
    py::str filler_str(filler);
    py::list apply_list;
    apply_list.append(dirname_result);
    apply_list.append(filler_str);

    set_ranges_on_splitted(path, ranges, apply_list, tx_map, false);
    return apply_list[0];
}

template<class StrType>
static py::tuple
forward_to_set_ranges_on_splitted(const char* function_name, const StrType& path, bool includeseparator = false)
{
    const auto ospath = py::module_::import("os.path");
    auto function = ospath.attr(function_name);
    auto function_result = function(path);

    const auto tx_map = initializer->get_tainting_map();
    if (not tx_map or tx_map->empty() or py::len(function_result) == 0) {
        return function_result;
    }

    auto [ranges, ranges_error] = get_ranges(path.ptr(), tx_map);
    if (ranges_error or ranges.empty()) {
        return function_result;
    }

    set_ranges_on_splitted(path, ranges, function_result, tx_map, includeseparator);
    return function_result;
}

template<class StrType>
py::tuple
api_ospathsplit_aspect(const StrType& path)
{
    return forward_to_set_ranges_on_splitted("split", path);
}

template<class StrType>
py::tuple
api_ospathsplitext_aspect(const StrType& path)
{
    return forward_to_set_ranges_on_splitted("splitext", path, true);
}

template<class StrType>
py::tuple
api_ospathsplitdrive_aspect(const StrType& path)
{
    return forward_to_set_ranges_on_splitted("splitdrive", path, true);
}

template<class StrType>
py::tuple
api_ospathsplitroot_aspect(const StrType& path)
{
    return forward_to_set_ranges_on_splitted("splitroot", path, true);
}

template<class StrType>
StrType
api_ospathnormcase_aspect(const StrType& path)
{
    const auto ospath = py::module_::import("os.path");
    auto normcase = ospath.attr("normcase");
    auto normcased = normcase(path);

    const auto tx_map = initializer->get_tainting_map();
    if (not tx_map or tx_map->empty()) {
        return normcased;
    }

    auto [ranges, ranges_error] = get_ranges(path.ptr(), tx_map);
    if (ranges_error or ranges.empty()) {
        return normcased;
    }

    const TaintRangeRefs result_ranges = ranges;
    if (PyObject* new_result = new_pyobject_id(normcased.ptr())) {
        set_ranges(new_result, result_ranges, tx_map);
        return py::reinterpret_steal<StrType>(new_result);
    }

    return normcased;
}

void
pyexport_ospath_aspects(py::module& m)
{
    m.def("_aspect_ospathjoin", &api_ospathjoin_aspect<py::str>, "first_part"_a, py::return_value_policy::move);
    m.def("_aspect_ospathjoin", &api_ospathjoin_aspect<py::bytes>, "first_part"_a, py::return_value_policy::move);
    m.def("_aspect_ospathnormcase", &api_ospathnormcase_aspect<py::str>, "path"_a, py::return_value_policy::move);
    m.def("_aspect_ospathnormcase", &api_ospathnormcase_aspect<py::bytes>, "path"_a, py::return_value_policy::move);
    m.def("_aspect_ospathbasename", &api_ospathbasename_aspect<py::str>, "path"_a, py::return_value_policy::move);
    m.def("_aspect_ospathbasename", &api_ospathbasename_aspect<py::bytes>, "path"_a, py::return_value_policy::move);
    m.def("_aspect_ospathdirname", &api_ospathdirname_aspect<py::str>, "path"_a, py::return_value_policy::move);
    m.def("_aspect_ospathdirname", &api_ospathdirname_aspect<py::bytes>, "path"_a, py::return_value_policy::move);
    m.def("_aspect_ospathsplit", &api_ospathsplit_aspect<py::str>, "path"_a, py::return_value_policy::move);
    m.def("_aspect_ospathsplit", &api_ospathsplit_aspect<py::bytes>, "path"_a, py::return_value_policy::move);
    m.def("_aspect_ospathsplitext", &api_ospathsplitext_aspect<py::str>, "path"_a, py::return_value_policy::move);
    m.def("_aspect_ospathsplitext", &api_ospathsplitext_aspect<py::bytes>, "path"_a, py::return_value_policy::move);
    m.def("_aspect_ospathsplitdrive", &api_ospathsplitdrive_aspect<py::str>, "path"_a, py::return_value_policy::move);
    m.def("_aspect_ospathsplitdrive", &api_ospathsplitdrive_aspect<py::bytes>, "path"_a, py::return_value_policy::move);
    m.def("_aspect_ospathsplitroot", &api_ospathsplitroot_aspect<py::str>, "path"_a, py::return_value_policy::move);
    m.def("_aspect_ospathsplitroot", &api_ospathsplitroot_aspect<py::bytes>, "path"_a, py::return_value_policy::move);
}
