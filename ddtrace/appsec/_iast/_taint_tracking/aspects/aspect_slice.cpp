#include "aspect_slice.h"
#include <algorithm>

/**
 * Optimized function to compute taint ranges for a slice operation.
 * This avoids creating an intermediate character-by-character index map,
 * directly computing overlapping ranges instead. This reduces memory usage
 * from O(n) to O(m) where n=string length, m=number of taint ranges.
 *
 * @param text The text object being sliced.
 * @param ranges The taint ranges of the original text.
 * @param start The start index of the slice (or nullptr/None).
 * @param stop The stop index of the slice (or nullptr/None).
 * @param step The step of the slice (or nullptr/None).
 *
 * @return Taint ranges for the sliced result.
 */
TaintRangeRefs
compute_slice_ranges(PyObject* text, const TaintRangeRefs& ranges, PyObject* start, PyObject* stop, PyObject* step)
{
    long length_text = static_cast<long>(py::len(text));

    // Parse slice parameters
    long start_int = 0;
    if (start != nullptr and start != Py_None) {
        start_int = PyLong_AsLong(start);
        if (start_int < 0) {
            start_int = length_text + start_int;
            if (start_int < 0) {
                start_int = 0;
            }
        }
    }

    long stop_int = length_text;
    if (stop != nullptr and stop != Py_None) {
        stop_int = PyLong_AsLong(stop);
        if (stop_int > length_text) {
            stop_int = length_text;
        } else if (stop_int < 0) {
            stop_int = length_text + stop_int;
            if (stop_int < 0) {
                stop_int = 0;
            }
        }
    }

    long step_int = 1;
    if (step != nullptr and step != Py_None) {
        step_int = PyLong_AsLong(step);
    }

    // For step != 1, we need to track which positions are included
    // Build a mapping of original positions to result positions
    if (step_int != 1) {
        // Use the original algorithm for non-unit steps (rare case)
        // Build position-to-range map only for the slice range
        std::vector<TaintRangePtr> position_map;
        position_map.reserve(stop_int - start_int);

        for (long i = start_int; i < stop_int; i += step_int) {
            TaintRangePtr range_at_pos = nullptr;
            for (const auto& taint_range : ranges) {
                if (i >= taint_range->start && i < (taint_range->start + taint_range->length)) {
                    range_at_pos = taint_range;
                    break;
                }
            }
            position_map.push_back(range_at_pos);
        }

        // Consolidate consecutive ranges
        TaintRangeRefs result_ranges;
        TaintRangePtr current_range = nullptr;
        size_t current_start = 0;

        for (size_t i = 0; i < position_map.size(); ++i) {
            if (position_map[i] != current_range) {
                if (current_range) {
                    result_ranges.emplace_back(safe_allocate_taint_range(
                      current_start, i - current_start, current_range->source, current_range->secure_marks));
                }
                current_range = position_map[i];
                current_start = i;
            }
        }
        if (current_range != nullptr) {
            result_ranges.emplace_back(safe_allocate_taint_range(
              current_start, position_map.size() - current_start, current_range->source, current_range->secure_marks));
        }

        return result_ranges;
    }

    // Optimized path for step == 1 (common case)
    // Directly compute overlapping ranges without intermediate array
    TaintRangeRefs result_ranges;

    for (const auto& taint_range : ranges) {
        long range_start = taint_range->start;
        long range_end = range_start + taint_range->length;

        // Check if this range overlaps with [start_int, stop_int)
        if (range_end <= start_int || range_start >= stop_int) {
            continue; // No overlap
        }

        // Compute the overlapping portion
        long overlap_start = std::max(range_start, start_int);
        long overlap_end = std::min(range_end, stop_int);

        // Translate to result coordinates (relative to start of slice)
        long result_start = overlap_start - start_int;
        long result_length = overlap_end - overlap_start;

        result_ranges.emplace_back(
          safe_allocate_taint_range(result_start, result_length, taint_range->source, taint_range->secure_marks));
    }

    return result_ranges;
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
    set_ranges(result_o, compute_slice_ranges(candidate_text, ranges, start, stop, step), ctx_map);
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

    CHECK_IAST_INITIALIZED_OR_RETURN(result_o);

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
