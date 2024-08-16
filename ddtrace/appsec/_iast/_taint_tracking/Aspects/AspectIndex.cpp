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

    cerr << "JJJ 2.1\n";

    try {
        PyObject* result_o = nullptr;
        cerr << "JJJ 2.2\n";
        PyObject* candidate_text = args[0];
        cerr << "JJJ 2.3\n";
        if (!is_text(candidate_text)) {
            cerr << "JJJ 2.4\n";
            const auto error =
              "The candidate text must be a string, not '" + std::string(Py_TYPE(candidate_text)->tp_name) + "'";
            cerr << "JJJ 3\n";
            iast_taint_log_error(error);
            py::set_error(PyExc_TypeError, error.c_str());
            return nullptr;
        }

        cerr << "JJJ 3.1\n";
        PyObject* idx = args[1];
        cerr << "JJJ 3.2\n";
        if (!is_some_number(idx)) {
            cerr << "JJJ 3.3\n";
            const auto error = "string indices must be integers, not '" + std::string(Py_TYPE(idx)->tp_name) + "'";
            cerr << "JJJ 3.4\n";
            cerr << "JJJ 4\n";
            iast_taint_log_error(error);
            py::set_error(PyExc_TypeError, error.c_str());
            return nullptr;
        }

        cerr << "JJJ 4.1\n";
        const auto ctx_map = initializer->get_tainting_map();
        cerr << "JJJ 4.2\n";
        result_o = PyObject_GetItem(candidate_text, idx);
        cerr << "JJJ 4.3\n";
        if (not ctx_map or ctx_map->empty()) {
            cerr << "JJJ 5\n";
            return result_o;
        }

        cerr << "JJJ 5.1\n";
        auto error_str = has_pyerr_as_string();
        cerr << "JJJ 5.2\n";
        if (!error_str.empty()) {
            error_str += " (native index_aspect)";
            cerr << "JJJ 5.3, error_cstr: " << error_str << "\n";
            iast_taint_log_error(error_str);
            cerr << "JJJ 6\n";
            py::set_error(PyExc_IndexError, error_str.c_str());
            return nullptr;
        }

        cerr << "JJJ 7\n";
        return index_aspect(result_o, candidate_text, idx, ctx_map);
    // } catch (const py::error_already_set& e) {
    //     const std::string error_message = "IAST propagation error in index_aspect. " + std::string(e.what());
    //     iast_taint_log_error(error_message);
    //     cerr << "JJJ 8\n";
    //     throw py::error_already_set();
    } catch (const std::exception& e) {
        const std::string error_message = "IAST propagation error in index_aspect. " + std::string(e.what());
        iast_taint_log_error(error_message);
        cerr << "JJJ 9\n";
        py::set_error(PyExc_TypeError, error_message.c_str());
        return nullptr;
    } catch (...) {
        const std::string error_message = "Unkown IAST propagation error in index_aspect. ";
        iast_taint_log_error(error_message);
        cerr << "JJJ 10\n";
        py::set_error(PyExc_TypeError, error_message.c_str());
        return nullptr;
    }
}
