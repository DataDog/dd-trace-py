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
                new_ranges.emplace_back(initializer->allocate_taint_range(current_start, index, current_range->source));
            }
            current_range = taint_range;
            current_start = index;
        }
    }
    if (current_range != nullptr) {
        new_ranges.emplace_back(initializer->allocate_taint_range(current_start, index, current_range->source));
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
    while (index < (long long)py::len(text)) {
        index_range_map.emplace_back(nullptr);
        index++;
    }
    py::list pylist{ py::cast(index_range_map) };
    auto res{ pylist[py::slice(start, stop, step)].cast<TaintRangeRefs>() };
    return res;
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
        return nullptr;
    }
    PyObject* candidate_text = args[0];
    PyObject* start = PyLong_FromLong(0);

    if (PyNumber_Check(args[1])) {
        start = PyNumber_Long(args[1]);
    }
    PyObject* stop = nullptr;
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
    if (slice == nullptr) {
        PyErr_Print();
        if (start != nullptr) {
            Py_DECREF(start);
        }
        if (stop != nullptr) {
            Py_DECREF(stop);
        }
        if (step != nullptr) {
            Py_DECREF(step);
        }
        return nullptr;
    }
    PyObject* result = PyObject_GetItem(candidate_text, slice);
    auto res = slice_aspect(result, candidate_text, start, stop, step);

    if (start != nullptr) {
        Py_DECREF(start);
    }
    if (stop != nullptr) {
        Py_DECREF(stop);
    }
    if (step != nullptr) {
        Py_DECREF(step);
    }
    Py_DECREF(slice);
    return res;
}