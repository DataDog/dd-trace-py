#include "AspectIndex.h"
#include "Helpers.h"
#include "Utils/StringUtils.h"
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
        cerr << "JJJ exit 1\n";
        return result_o;
    }

    if (is_text(candidate_text)) {
        for (const auto& current_range : ranges) {
            if (current_range->start <= idx_long and idx_long < (current_range->start + current_range->length)) {
                ranges_to_set.emplace_back(initializer->allocate_taint_range(0l, 1l, current_range->source));
                break;
            }
        }

    } else if (PyReMatch_Check(candidate_text)) { // For re.Match objects, taint the whole output
        try {
            const size_t& len_result_o{ get_pyobject_size(result_o) };
            const auto& current_range = ranges.at(0);
            ranges_to_set.emplace_back(initializer->allocate_taint_range(0l, len_result_o, current_range->source));
        } catch (const std::out_of_range& ex) {
            if (nullptr == result_o) {
                py::set_error(PyExc_IndexError, "Index out of range");
            }
            // No ranges found, return original object
            cerr << "JJJ exit 2\n";
            return result_o;
        }
    } else { // For other types nothing to do
        cerr << "JJJ exit 3\n";
        return result_o;
    }

    const auto& res_new_id = new_pyobject_id(result_o);
    Py_DecRef(result_o);

    if (ranges_to_set.empty()) {
        cerr << "JJJ exit 4\n";
        return res_new_id;
    }
    set_ranges(res_new_id, ranges_to_set, tx_taint_map);

    cerr << "JJJ exit 5\n";
    return res_new_id;
}

PyObject*
api_index_aspect(PyObject* self, PyObject* const* args, const Py_ssize_t nargs)
{
    if (nargs != 2) {
        py::set_error(PyExc_ValueError, MSG_ERROR_N_PARAMS);
        iast_taint_log_error(MSG_ERROR_N_PARAMS);
        return nullptr;
    }

    PyObject* candidate_text = args[0];
    PyObject* idx = args[1];
    auto result_o = PyObject_GetItem(candidate_text, idx);
    if (!is_tainteable(candidate_text) or !is_some_number(idx)) {
        return result_o;
    }
    TRY_CATCH_ASPECT("index_aspect", return result_o, , {
        const auto ctx_map = Initializer::get_tainting_map();
        if (not ctx_map or ctx_map->empty()) {
            return result_o;
        }

        auto error_str = has_pyerr_as_string();
        if (!error_str.empty()) {
            error_str += " (native index_aspect)";
            iast_taint_log_error(error_str);
            py::set_error(PyExc_IndexError, error_str.c_str());
            return nullptr;
        }

        return index_aspect(result_o, candidate_text, idx, ctx_map);
    });
}
