#include "AspectSlice.h"

TaintRangeRefs
reduce_ranges_from_index_range_map(TaintRangeRefs index_range_map)
{
    TaintRangeRefs new_ranges;
    TaintRangePtr current_range;
    size_t current_start = 0;
    size_t index;

    for (index = 0; index < index_range_map.size(); ++index) {
        auto taint_range{ index_range_map.at(index) };
        if (taint_range != current_range) {
            if (current_range) {
                new_ranges.emplace_back(
                  initializer->allocate_taint_range(current_start, index - current_start, current_range->source));
            }
            current_range = taint_range;
            current_start = index;
        }
    }
    if (current_range != NULL) {
        new_ranges.emplace_back(
          initializer->allocate_taint_range(current_start, index - current_start, current_range->source));
    }
    return new_ranges;
}

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
    long length_text = (long long)py::len(text);
    while (index < length_text) {
        index_range_map.emplace_back(nullptr);
        index++;
    }
    TaintRangeRefs index_range_map_result;
    long start_int = PyLong_AsLong(start);
    long stop_int = length_text;
    if (stop != NULL) {
        stop_int = PyLong_AsLong(stop);
        if (stop_int > length_text) {
            stop_int = length_text;
        } else if (stop_int < 0) {
            stop_int = length_text + stop_int;
        }
    }
    long step_int = 1;
    if (step != NULL) {
        step_int = PyLong_AsLong(step);
    }
    for (auto i = start_int; i < stop_int; i += step_int) {
        index_range_map_result.emplace_back(index_range_map[i]);
    }

    return index_range_map_result;
}

PyObject*
slice_aspect(PyObject* result_o, PyObject* candidate_text, PyObject* start, PyObject* stop, PyObject* step)
{
    auto ranges = get_ranges(candidate_text);
    if (ranges.empty()) {
        return result_o;
    }
    set_ranges(result_o,
               reduce_ranges_from_index_range_map(build_index_range_map(candidate_text, ranges, start, stop, step)));
    return result_o;
}

PyObject*
api_slice_aspect(PyObject* self, PyObject* const* args, Py_ssize_t nargs)
{
    if (nargs < 3) {
        return NULL;
    }
    PyObject* candidate_text = args[0];
    PyObject* start = PyLong_FromLong(0);

    if (PyNumber_Check(args[1])) {
        start = PyNumber_Long(args[1]);
    }
    PyObject* stop = NULL;
    if (PyNumber_Check(args[2])) {
        stop = PyNumber_Long(args[2]);
    }
    PyObject* step = PyLong_FromLong(1);
    if (nargs == 4) {
        if (PyNumber_Check(args[3])) {
            step = PyNumber_Long(args[3]);
        }
    }

    PyObject* slice = PySlice_New(start, stop, step);
    if (slice == NULL) {
        PyErr_Print();
        if (start != NULL) {
            Py_DecRef(start);
        }
        if (stop != NULL) {
            Py_DecRef(stop);
        }
        if (step != NULL) {
            Py_DecRef(step);
        }
        return NULL;
    }
    PyObject* result = PyObject_GetItem(candidate_text, slice);

    auto res = slice_aspect(result, candidate_text, start, stop, step);

    if (start != NULL) {
        Py_DecRef(start);
    }
    if (stop != NULL) {
        Py_DecRef(stop);
    }
    if (step != NULL) {
        Py_DecRef(step);
    }
    Py_DecRef(slice);
    return res;
}