#include "aspect_operator_add.h"
#include "helpers.h"

/**
 * This function updates result_o object with taint information of candidate_text and/or text_to_add
 *
 * @param result_o The result object to which the aspect will be added.
 * @param candidate_text The candidate text object to which the aspect will be added.
 * @param text_to_add The text aspect to be added.
 * @param tx_taint_map The taint range map that stores taint information.
 *
 * @return A new result object with the taint information.
 */
PyObject*
add_aspect(PyObject* result_o,
           PyObject* candidate_text,
           PyObject* text_to_add,
           const TaintRangeMapTypePtr& tx_taint_map)
{
    const size_t len_candidate_text{ get_pyobject_size(candidate_text) };
    const size_t len_text_to_add{ get_pyobject_size(text_to_add) };

    // Appended from or with nothing, ranges didn't change
    if ((len_text_to_add == 0 and len_candidate_text > 0) ||
        (len_text_to_add > 0 and len_candidate_text == 0 and text_to_add == result_o)) {
        return result_o;
    }

    const auto& to_candidate_text = get_tainted_object(candidate_text, tx_taint_map);
    if (to_candidate_text and to_candidate_text->get_ranges().size() >= TaintedObject::TAINT_RANGE_LIMIT) {
        const auto& res_new_id = new_pyobject_id(result_o);
        Py_DECREF(result_o);
        // If left side is already at the maximum taint ranges, we just reuse its
        // ranges, we don't need to look at left side.
        set_tainted_object(res_new_id, to_candidate_text, tx_taint_map);
        return res_new_id;
    }

    const auto& to_text_to_add = get_tainted_object(text_to_add, tx_taint_map);
    if (!to_candidate_text and !to_text_to_add) {
        return result_o;
    }
    if (!to_text_to_add) {
        const auto& res_new_id = new_pyobject_id(result_o);
        Py_DECREF(result_o);
        set_tainted_object(res_new_id, to_candidate_text, tx_taint_map);
        return res_new_id;
    }

    if (!to_candidate_text) {
        const auto tainted = initializer->allocate_tainted_object();
        tainted->add_ranges_shifted(to_text_to_add, static_cast<RANGE_START>(len_candidate_text));
        set_tainted_object(result_o, tainted, tx_taint_map);
    }

    // At this point we have both to_candidate_text and to_text_to_add to we add the
    // ranges from both to result_o
    const auto tainted = initializer->allocate_tainted_object_copy(to_candidate_text);
    tainted->add_ranges_shifted(to_text_to_add, static_cast<RANGE_START>(len_candidate_text));
    set_tainted_object(result_o, tainted, tx_taint_map);

    return result_o;
}

/**
 * Adds aspect, override all python Add operations.
 *
 * The AST Visitor (ddtrace/appsec/_iast/_ast/visitor.py) replaces all "+" operations in Python code with this function.
 * This function takes 2 arguments. If the operation is 'a = b + c', this function should be 'a = api_add_aspect(b, c)'.
 * This function connects Python with the C++ function 'add_aspect'.
 *
 * @param self The Python extension module.
 * @param args An array of Python objects containing the candidate text and text aspect.
 * @param nargs The number of arguments in the 'args' array.
 *
 * @return A new Python object representing the result of adding the aspect to the candidate text, considering taint
 * information.
 */
PyObject*
api_add_aspect(PyObject* self, PyObject* const* args, Py_ssize_t nargs)
{
    PyObject* result_o = nullptr;

    if (nargs != 2) {
        py::set_error(PyExc_ValueError, MSG_ERROR_N_PARAMS);
        return nullptr;
    }
    PyObject* candidate_text = args[0];
    PyObject* text_to_add = args[1];

    // PyNumber_Add actually works for any type!
    result_o = PyNumber_Add(candidate_text, text_to_add);
    if (result_o == nullptr) {
        return nullptr;
    }

    TRY_CATCH_ASPECT("add_aspect", return result_o, , {
        const auto tx_map = Initializer::get_tainting_map();
        if (not tx_map or tx_map->empty()) {
            return result_o;
        }

        if (not args_are_text_and_same_type(candidate_text, text_to_add)) {
            return result_o;
        }

        // Early return if there are no ranges
        auto [ranges_candidate_text, ranges_error_canditate_text] = get_ranges(candidate_text, tx_map);
        auto [ranges_text_to_add, ranges_error_text_to_add] = get_ranges(text_to_add, tx_map);
        if (ranges_error_canditate_text or ranges_error_text_to_add or
            (ranges_candidate_text.empty() and ranges_text_to_add.empty())) {
            return result_o;
        }

        // Quickly skip if both are noninterned-unicodes and not tainted
        if (is_notinterned_notfasttainted_unicode(candidate_text) &&
            is_notinterned_notfasttainted_unicode(text_to_add)) {
            return result_o;
        }

        return add_aspect(result_o, candidate_text, text_to_add, tx_map);
    });
}

PyObject*
api_add_inplace_aspect(PyObject* self, PyObject* const* args, Py_ssize_t nargs)
{
    if (nargs != 2) {
        py::set_error(PyExc_ValueError, MSG_ERROR_N_PARAMS);
        return nullptr;
    }
    PyObject* candidate_text = args[0];
    PyObject* text_to_add = args[1];

    PyObject* result_o = PyNumber_InPlaceAdd(candidate_text, text_to_add);
    if (result_o == nullptr) {
        return nullptr;
    }

    TRY_CATCH_ASPECT("add_inplace_aspect", return result_o, , {
        const auto tx_map = Initializer::get_tainting_map();
        if (not tx_map or tx_map->empty()) {
            return result_o;
        }

        if (not args_are_text_and_same_type(candidate_text, text_to_add)) {
            return result_o;
        }

        // Early return if there are no ranges
        auto [ranges_candidate_text, ranges_error_canditate_text] = get_ranges(candidate_text, tx_map);
        if (ranges_error_canditate_text) {
            return result_o;
        }
        auto [ranges_text_to_add, ranges_error_text_to_add] = get_ranges(text_to_add, tx_map);
        if (ranges_error_text_to_add or (ranges_candidate_text.empty() and ranges_text_to_add.empty())) {
            return result_o;
        }

        // Quickly skip if both are noninterned-unicodes and not tainted
        if (is_notinterned_notfasttainted_unicode(candidate_text) &&
            is_notinterned_notfasttainted_unicode(text_to_add)) {
            return result_o;
        }
        return add_aspect(result_o, candidate_text, text_to_add, tx_map);
    });
}
