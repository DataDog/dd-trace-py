#include <pybind11/stl.h>

#include "AspectIndex.h"

PyObject*
index_aspect(PyObject* result_o, PyObject* candidate_text, PyObject* idx, TaintRangeMapType* tx_taint_map)
{
    auto idx_long = PyLong_AsLong(idx);
    TaintRangeRefs ranges_to_set;
    auto ranges = get_ranges(candidate_text);
    for (const auto& current_range : ranges) {
        if (current_range->start <= idx_long and idx_long < (current_range->start + current_range->length)) {
            ranges_to_set.emplace_back(initializer->allocate_taint_range(0l, 1l, current_range->source));
            break;
        }
    }

    const auto& res_new_id = new_pyobject_id(result_o);
    Py_DECREF(result_o);

    if (ranges_to_set.empty()) {
        return res_new_id;
    }
    set_ranges(res_new_id, ranges_to_set);

    return res_new_id;
}

PyObject*
api_index_aspect(PyObject* self, PyObject* const* args, Py_ssize_t nargs)
{
    if (nargs != 2) {
        return nullptr;
    }
    PyObject* candidate_text = args[0];
    PyObject* idx = args[1];

    PyObject* result_o;

    result_o = PyObject_GetItem(candidate_text, idx);
    auto ctx_map = initializer->get_tainting_map();
    if (not ctx_map or ctx_map->empty()) {
        return result_o;
    }
    auto res = index_aspect(result_o, candidate_text, idx, ctx_map);
    return res;
}
