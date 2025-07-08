#include "aspect_extend.h"
#include "helpers.h"

/**
 * @brief Taint candidate_text when bytearray extends is called.
 *
 * @param self
 * @param args: 0: candidate text, 1: Bytearray or bytes to extend in candidate text
 * @param nargs number of elements in args
 * @return PyObject*: return None (Remember, Pyobject None isn't the same as nullptr)
 */
PyObject*
api_extend_aspect(PyObject* self, PyObject* const* args, const Py_ssize_t nargs)
{
    if (nargs != 2 or !args) {
        throw py::value_error(MSG_ERROR_N_PARAMS);
    }

    PyObject* candidate_text = args[0];
    if (!PyByteArray_Check(candidate_text)) {
        py::set_error(PyExc_TypeError, "The candidate text must be a bytearray.");
        return nullptr;
    }
    auto len_candidate_text = PyByteArray_Size(candidate_text);
    PyObject* to_add = args[1];

    if (!PyByteArray_Check(to_add) and !PyBytes_Check(to_add)) {
        py::set_error(PyExc_TypeError, "The text to add must be a bytearray or bytes.");
        return nullptr;
    }

    auto ctx_map = Initializer::get_tainting_map();
    if (not ctx_map or ctx_map->empty()) {
        auto method_name = PyUnicode_FromString("extend");
        PyObject_CallMethodObjArgs(candidate_text, method_name, to_add, nullptr);
        if (has_pyerr()) {
            Py_DecRef(method_name);
            return nullptr;
        }
        Py_DecRef(method_name);
    } else {
        const auto& to_candidate = get_tainted_object(candidate_text, ctx_map);
        auto to_result = initializer->allocate_tainted_object_copy(to_candidate);
        const auto& to_toadd = get_tainted_object(to_add, ctx_map);

        // Ensure no returns are done before this method call
        auto method_name = PyUnicode_FromString("extend");
        PyObject_CallMethodObjArgs(candidate_text, method_name, to_add, nullptr);
        if (has_pyerr()) {
            Py_DecRef(method_name);
            return nullptr;
        }
        Py_DecRef(method_name);

        if (to_result == nullptr) {
            Py_RETURN_NONE;
        }

        if (to_toadd) {
            to_result->add_ranges_shifted(to_toadd, (long)len_candidate_text);
        }
        set_tainted_object(candidate_text, to_result, ctx_map);
    }
    Py_RETURN_NONE;
}
