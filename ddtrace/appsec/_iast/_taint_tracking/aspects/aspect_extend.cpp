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
        py::set_error(PyExc_ValueError, MSG_ERROR_N_PARAMS);
        return nullptr;
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

    const auto ctx_map = safe_get_tainted_object_map_from_list_of_pyobjects({ candidate_text, to_add });
    TaintedObjectPtr to_result;
    TaintedObjectPtr to_toadd;
    if (ctx_map and !ctx_map->empty()) {
        const auto& to_candidate = get_tainted_object(candidate_text, ctx_map);
        to_result = safe_allocate_tainted_object_copy(to_candidate);
        to_toadd = get_tainted_object(to_add, ctx_map);
    }

    py::object method_name = py::reinterpret_steal<py::object>(PyUnicode_FromString("extend"));
    if (!method_name) {
        return nullptr;
    }
    py::object extend_result =
      py::reinterpret_steal<py::object>(PyObject_CallMethodObjArgs(candidate_text, method_name.ptr(), to_add, nullptr));
    if (!extend_result) {
        return nullptr;
    }

    if (!to_result) {
        Py_RETURN_NONE;
    }

    if (to_toadd) {
        to_result->add_ranges_shifted(to_toadd, (long)len_candidate_text);
    }
    set_tainted_object(candidate_text, to_result, ctx_map);
    Py_RETURN_NONE;
}
