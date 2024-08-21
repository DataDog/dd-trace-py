#include "AspectSlice.h"
#include <iostream> // JJJ

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
                new_ranges.emplace_back(
                  initializer->allocate_taint_range(current_start, index - current_start, current_range->source));
            }
            current_range = taint_range;
            current_start = index;
        }
    }
    if (current_range != nullptr) {
        new_ranges.emplace_back(
          initializer->allocate_taint_range(current_start, index - current_start, current_range->source));
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
    TaintRangeRefs index_range_map_result;
    long start_int = PyLong_AsLong(start);
    if (start_int < 0) {
        start_int = length_text + start_int;
        if (start_int < 0) {
            start_int = 0;
        }
    }
    long stop_int = length_text;
    if (stop != nullptr) {
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
    if (step != nullptr) {
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
    cerr << "JJJ slice 1\n";
    auto ctx_map = initializer->get_tainting_map();

    cerr << "JJJ slice 2\n";
    if (not ctx_map or ctx_map->empty()) {
        cerr << "JJJ slice 3\n";
        return result_o;
    }
    cerr << "JJJ slice 4\n";
    auto [ranges, ranges_error] = get_ranges(candidate_text, ctx_map);
    if (ranges_error or ranges.empty()) {
        cerr << "JJJ slice 5\n";
        return result_o;
    }
    cerr << "JJJ slice 6\n";
    set_ranges(result_o,
               reduce_ranges_from_index_range_map(build_index_range_map(candidate_text, ranges, start, stop, step)),
               ctx_map);
    cerr << "JJJ slice 7\n";
    return result_o;
}

// JJJ
void
printPyObject(PyObject* obj)
{
    // Assuming you're in an environment where Python has been initialized.
    // If you're in an embedded Python scenario, ensure you're within a py::scoped_interpreter guard{}

    // Wrap the PyObject* in a py::object
    py::object py_obj = py::reinterpret_borrow<py::object>(obj);

    // Now you can use py::print
    py::print(py_obj);
}

PyObject*
api_slice_aspect(PyObject* self, PyObject* const* args, Py_ssize_t nargs)
{
    cerr << "JJJ nargs: " << nargs << endl; // JJJ
    printPyObject(args[0]);
    printPyObject(args[1]);
    printPyObject(args[2]);

    if (nargs < 3) {
        py::set_error(PyExc_ValueError, MSG_ERROR_N_PARAMS);
        iast_taint_log_error(MSG_ERROR_N_PARAMS);
        cerr << "JJJ 1\n";
        return nullptr;
    }

    cerr << "JJJ 2\n";
    try {
        cerr << "JJJ 3\n";
        PyObject* candidate_text = args[0];
        PyObject* start = PyLong_Check(args[1]) ? PyNumber_Long(args[1]) : PyLong_FromLong(0);
        PyObject* stop = PyLong_Check(args[2]) ? PyNumber_Long(args[2]) : Py_None;

        PyObject* step = PyLong_FromLong(1);
        if (nargs == 4 and PyLong_Check(args[3])) {
            step = PyNumber_Long(args[3]);
        }

        cerr << "JJJ 4\n";
        PyObject* slice = PySlice_New(start, stop, step);
        if (slice == nullptr) {
            cerr << "JJJ 5\n";
            PyErr_Print();
            Py_XDECREF(start);
            Py_XDECREF(step);
            return nullptr;
        }
        cerr << "JJJ 6\n";
        PyObject* result_o = PyObject_GetItem(candidate_text, slice);
        if (result_o == nullptr or (get_unique_id(result_o) == get_unique_id(candidate_text))) {
            // Result is nullptr or same as candidate text, so either it's already tainted or not, but no changes
            // needed
            cerr << "JJJ 7\n";
            cerr << "JJJ 7.1, result_o = " << result_o << endl; // JJJ
            cerr << "JJJ 7.2\n";
            Py_XDECREF(start);
            Py_XDECREF(step);
            Py_XDECREF(slice);
            cerr << "JJJ 7.3\n";
            return result_o;
        }

        if (!is_text(candidate_text) or (start != Py_None and !PyLong_Check(start)) or
            (stop != Py_None and !PyLong_Check(stop)) or (step != Py_None and !PyLong_Check(step))) {
            cerr << "JJJ 7.4\n";
            Py_XDECREF(start);
            Py_XDECREF(step);
            Py_XDECREF(slice);
            return result_o;
        }

        cerr << "JJJ 8\n";
        auto res = slice_aspect(result_o, candidate_text, start, stop, step);

        Py_XDECREF(start);
        Py_XDECREF(step);
        Py_XDECREF(slice);
        Py_XDECREF(result_o);

        cerr << "JJJ 9\n";
        return res;
    } catch (const std::exception& e) {
        cerr << "JJJ exc1\n";
        const std::string error_message = "IAST propagation error in slice_aspect. " + std::string(e.what());
        iast_taint_log_error(error_message);
        py::set_error(PyExc_TypeError, error_message.c_str());
        return nullptr;
    } catch (...) {
        cerr << "JJJ exc2\n";
        const std::string error_message = "Unkown IAST propagation error in slice_aspect. ";
        iast_taint_log_error(error_message);
        py::set_error(PyExc_TypeError, error_message.c_str());
        return nullptr;
    }
}