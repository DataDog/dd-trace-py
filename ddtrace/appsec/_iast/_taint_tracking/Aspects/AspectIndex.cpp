#include "AspectIndex.h"
#include "Helpers.h"

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
    if (nargs != 2) {
        py::set_error(PyExc_ValueError, MSG_ERROR_N_PARAMS);
        return nullptr;
    }

    PyObject* result_o = nullptr;

    try {
        auto ctx_map = initializer->get_tainting_map();

        PyObject* candidate_text = args[0];
        if (!is_text(candidate_text)) {
            py::set_error(PyExc_TypeError, "The candidate text must be a string.");
            return nullptr;
        }

        PyObject* idx = args[1];
        if (!is_some_number(idx)) {
            py::set_error(PyExc_TypeError, "The index must be a number.");
            return nullptr;
        }

        result_o = PyObject_GetItem(candidate_text, idx);
        if (not ctx_map or ctx_map->empty()) {
            return result_o;
        }

        if (has_pyerr()) {
            return nullptr;
        }

        return index_aspect(result_o, candidate_text, idx, ctx_map);
    } catch (const py::error_already_set& e) {
        const std::string error_message = "IAST propagation error in index_aspect. " + std::string(e.what());
        iast_taint_log_error(error_message);
        return result_o;
    } catch (const std::exception& e) {
        const std::string error_message = "IAST propagation error in index_aspect. " + std::string(e.what());
        iast_taint_log_error(error_message);
        return result_o;
    } catch (...) {
        const std::string error_message = "Unkown IAST propagation error in index_aspect. ";
        iast_taint_log_error(error_message);
        return result_o;
    }
}
