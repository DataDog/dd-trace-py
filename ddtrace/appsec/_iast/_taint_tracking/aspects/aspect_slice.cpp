#include "aspect_slice.h"
#include "helpers.h"

/**
 * This function reduces the taint ranges from the given index range map.
 *
 * @param index_range_map The index range map from which the taint ranges are to be reduced.
 *
 * @return A map of taint ranges for the given index range map.
 */
TaintRangeRefs
reduce_ranges_from_index_range_map(const TaintRangeRefs& index_range_map)
{
    TaintRangeRefs new_ranges;
    TaintRangePtr current_range;
    size_t current_start = 0;
    size_t index;

    for (index = 0; index < index_range_map.size(); ++index) {
        if (const auto& taint_range{ index_range_map.at(index) }; taint_range != current_range) {
            if (current_range) {
                new_ranges.emplace_back(initializer->allocate_taint_range(
                  current_start, index - current_start, current_range->source, current_range->secure_marks));
            }
            current_range = taint_range;
            current_start = index;
        }
    }
    if (current_range != nullptr) {
        new_ranges.emplace_back(initializer->allocate_taint_range(
          current_start, index - current_start, current_range->source, current_range->secure_marks));
    }
    return new_ranges;
}

/**
 * This function builds a map of taint ranges for the given text object.
 *
 * @param text The text object for which the taint ranges are to be built.
 * @param ranges The taint range map that stores taint information.
 * @param start The start index of the text object.
 * @param stop The stop index of the text object.
 * @param step The step index of the text object.
 *
 * @return A map of taint ranges for the given text object.
 */
TaintRangeRefs
build_index_range_map(PyObject* text, TaintRangeRefs& ranges, PyObject* start, PyObject* stop, PyObject* step)
{
    TaintRangeRefs index_range_map;
    long long index = 0;
    for (const auto& taint_range : ranges) {
        auto shared_range = taint_range;
        while (index < taint_range->start) {
            index_range_map.emplace_back(nullptr);
            index++;
        }
        while (index < (taint_range->start + taint_range->length)) {
            index_range_map.emplace_back(shared_range);
            index++;
        }
    }
    long length_text = static_cast<long long>(py::len(text));
    while (index < length_text) {
        index_range_map.emplace_back(nullptr);
        index++;
    }
    PyObject* slice = PySlice_New(
      (start != nullptr) ? start : Py_None, (stop != nullptr) ? stop : Py_None, (step != nullptr) ? step : Py_None);
    if (slice == nullptr) {
        return {};
    }

    Py_ssize_t norm_start, norm_stop, norm_step, slicelength;
    if (PySlice_GetIndicesEx(slice, length_text, &norm_start, &norm_stop, &norm_step, &slicelength) < 0) {
        Py_DECREF(slice);
        return {};
    }
    Py_DECREF(slice);

    TaintRangeRefs index_range_map_result;
    Py_ssize_t map_size = static_cast<Py_ssize_t>(index_range_map.size());
    for (Py_ssize_t i = 0; i < slicelength; i++) {
        Py_ssize_t idx = norm_start + i * norm_step;
        if (idx >= 0 && idx < map_size) {
            index_range_map_result.emplace_back(index_range_map[idx]);
        }
    }

    return index_range_map_result;
}

PyObject*
slice_aspect(PyObject* result_o, PyObject* candidate_text, PyObject* start, PyObject* stop, PyObject* step)
{
    const auto ctx_map = safe_get_tainted_object_map(candidate_text);

    if (not ctx_map or ctx_map->empty()) {
        return result_o;
    }
    auto [ranges, ranges_error] = get_ranges(candidate_text, ctx_map);
    if (ranges_error or ranges.empty()) {
        return result_o;
    }
    set_ranges(result_o,
               reduce_ranges_from_index_range_map(build_index_range_map(candidate_text, ranges, start, stop, step)),
               ctx_map);
    return result_o;
}

PyObject*
api_slice_aspect(PyObject* self, PyObject* const* args, Py_ssize_t nargs)
{
    if (nargs < 3) {
        py::set_error(PyExc_ValueError, MSG_ERROR_N_PARAMS);
        iast_taint_log_error(MSG_ERROR_N_PARAMS);
        return nullptr;
    }

    PyObject* candidate_text = args[0];
    PyObject* start = args[1];
    PyObject* stop = args[2];
    PyObject* step = nullptr;
    if (nargs == 4)
        step = args[3];

    PyObject* slice = PySlice_New(start, stop, step);
    if (slice == nullptr) {
        PyErr_Print();
        return nullptr;
    }

    PyObject* result_o = PyObject_GetItem(candidate_text, slice);

    TRY_CATCH_ASPECT("slice_aspect", return result_o, Py_XDECREF(slice), {
        // If no result or the params are not None|Number or the result is the same as the candidate text, nothing
        // to taint
        if (result_o == nullptr or (!is_text(candidate_text)) or (start != Py_None and !PyLong_Check(start)) or
            (stop != Py_None and !PyLong_Check(stop)) or (step != Py_None and !PyLong_Check(step)) or
            (get_unique_id(result_o) == get_unique_id(candidate_text))) {
            Py_XDECREF(slice);
            return result_o;
        }

        auto res = slice_aspect(result_o, candidate_text, start, stop, step);
        Py_XDECREF(slice);
        return res;
    });
}