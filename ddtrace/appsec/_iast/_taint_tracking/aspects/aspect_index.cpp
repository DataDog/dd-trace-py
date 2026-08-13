#include "aspect_index.h"

/**
 * @brief Index aspect
 *
 * @param result_o
 * @param candidate_text
 * @param idx
 * @param ranges
 * @param tx_taint_map
 */
static void
index_aspect_owned(py::object& result_o,
                   const PyObject* candidate_text,
                   PyObject* idx,
                   const TaintRangeRefs& ranges,
                   const TaintedObjectMapTypePtr& tx_map)
{
    TaintRangeRefs ranges_to_set;

    if (is_text(candidate_text)) {
        for (const auto& current_range : ranges) {
            const auto idx_long = PyLong_AsLong(idx);
            if (current_range->start <= idx_long and idx_long < (current_range->start + current_range->length)) {
                ranges_to_set.emplace_back(
                  safe_allocate_taint_range(0l, 1l, current_range->source, current_range->secure_marks));
                break;
            }
        }

    } else if (PyReMatch_Check(candidate_text)) { // For re.Match objects, taint the whole output
        try {
            const size_t& len_result_o{ get_pyobject_size(result_o.ptr()) };
            const auto& current_range = ranges.at(0);
            ranges_to_set.emplace_back(
              safe_allocate_taint_range(0l, len_result_o, current_range->source, current_range->secure_marks));
        } catch (const std::out_of_range& ex) {
            if (!result_o) {
                throw py::index_error();
            }
            // No ranges found, return original object
            return;
        }
    } else {
        // Other stuff
        return;
    }

    py::object res_new_id = new_pyobject_id_owned(result_o);
    if (!res_new_id) {
        PyErr_Clear();
        return;
    }

    if (!ranges_to_set.empty()) {
        set_ranges(res_new_id.ptr(), ranges_to_set, tx_map);
    }
    result_o = std::move(res_new_id);
}

PyObject*
index_aspect(PyObject* result_o,
             const PyObject* candidate_text,
             PyObject* idx,
             const TaintRangeRefs& ranges,
             const TaintedObjectMapTypePtr& tx_map)
{
    py::object owned_result = py::reinterpret_steal<py::object>(result_o);
    index_aspect_owned(owned_result, candidate_text, idx, ranges, tx_map);
    return owned_result.release().ptr();
}

PyObject*
api_index_aspect(PyObject* self, PyObject* const* args, const Py_ssize_t nargs)
{
    if (nargs != 2) {
        iast_taint_log_error(MSG_ERROR_N_PARAMS);
        py::set_error(PyExc_ValueError, MSG_ERROR_N_PARAMS);
        return nullptr;
    }

    if (args == nullptr) {
        return nullptr;
    }

    PyObject* candidate_text = args[0];
    PyObject* idx = args[1];
    py::object result_o = py::reinterpret_steal<py::object>(PyObject_GetItem(candidate_text, idx));
    if (!result_o) {
        return nullptr;
    }

    if (!taint_engine_context || !initializer) {
        return result_o.release().ptr();
    }

    TRY_CATCH_ASPECT("index_aspect", return result_o.release().ptr(), , {
        const auto tx_map = safe_get_tainted_object_map(candidate_text);
        if (tx_map == nullptr or tx_map->empty()) {
            return result_o.release().ptr();
        }

        auto [ranges, ranges_error] = get_ranges(candidate_text, tx_map);
        if (ranges_error or ranges.empty()) {
            return result_o.release().ptr();
        }

        if (const auto error = has_pyerr_as_string(); !error.empty()) {
            iast_taint_log_error(error);
            return nullptr;
        }

        if ((!is_text(candidate_text) or !is_some_number(idx)) and !PyReMatch_Check(candidate_text)) {
            return result_o.release().ptr();
        }

        index_aspect_owned(result_o, candidate_text, idx, ranges, tx_map);
        return result_o.release().ptr();
    });
}
