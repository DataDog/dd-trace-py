#include "AspectIndex.h"
#include "Helpers.h"
#include <iostream> // JJJ

/**
 * @brief Index aspect
 *
 * @param result_o
 * @param candidate_text
 * @param idx
 * @param tx_taint_map
 * @return PyObject*
 */
PyObject*
index_aspect(PyObject* result_o, PyObject* candidate_text, PyObject* idx, const TaintRangeMapTypePtr& tx_taint_map)
{
    const auto idx_long = PyLong_AsLong(idx);
    TaintRangeRefs ranges_to_set;
    auto [ranges, ranges_error] = get_ranges(candidate_text, tx_taint_map);
    if (ranges_error) {
        return result_o;
    }
    for (const auto& current_range : ranges) {
        if (current_range->start <= idx_long and idx_long < (current_range->start + current_range->length)) {
            ranges_to_set.emplace_back(initializer->allocate_taint_range(0l, 1l, current_range->source));
            break;
        }
    }

    const auto& res_new_id = new_pyobject_id(result_o);
    Py_DecRef(result_o);

    if (ranges_to_set.empty()) {
        return res_new_id;
    }
    set_ranges(res_new_id, ranges_to_set, tx_taint_map);

    return res_new_id;
}

PyObject*
api_index_aspect(PyObject* self, PyObject* const* args, const Py_ssize_t nargs)
{
    cerr << "JJJ 1\n";
    if (nargs != 2) {
        py::set_error(PyExc_ValueError, MSG_ERROR_N_PARAMS);
    cerr << "JJJ 2\n";
        iast_taint_log_error(MSG_ERROR_N_PARAMS);
        return nullptr;
    }

    PyObject* result_o = nullptr;

    try {
        PyObject* candidate_text = args[0];
        if (!is_text(candidate_text)) {
            const auto error = "The candidate text must be a string, not '" + std::string(Py_TYPE(candidate_text)->tp_name) + "'";
            py::set_error(PyExc_TypeError, error.c_str());
    cerr << "JJJ 3\n";
            iast_taint_log_error(error);
            return nullptr;
        }

        PyObject* idx = args[1];
        if (!is_some_number(idx)) {
            const auto error = "string indices must be integers, not '" + std::string(Py_TYPE(idx)->tp_name) + "'";
            py::set_error(PyExc_TypeError, error.c_str());
    cerr << "JJJ 4\n";
            iast_taint_log_error(error);
            return nullptr;
        }

        auto ctx_map = initializer->get_tainting_map();
        result_o = PyObject_GetItem(candidate_text, idx);
        if (not ctx_map or ctx_map->empty()) {
    cerr << "JJJ 5\n";
            return result_o;
        }

        auto error_cstr = has_pyerr_as_cstring();
        if (error_cstr) {
            iast_taint_log_error(std::string(error_cstr));
    cerr << "JJJ 6\n";
            return nullptr;
        }

    cerr << "JJJ 7\n";
        return index_aspect(result_o, candidate_text, idx, ctx_map);
    } catch (const py::error_already_set& e) {
        const std::string error_message = "IAST propagation error in index_aspect. " + std::string(e.what());
        iast_taint_log_error(error_message);
    cerr << "JJJ 8\n";
        return result_o;
    } catch (const std::exception& e) {
        const std::string error_message = "IAST propagation error in index_aspect. " + std::string(e.what());
        iast_taint_log_error(error_message);
    cerr << "JJJ 9\n";
        return result_o;
    } catch (...) {
        const std::string error_message = "Unkown IAST propagation error in index_aspect. ";
        iast_taint_log_error(error_message);
    cerr << "JJJ 10\n";
        return result_o;
    }
}
