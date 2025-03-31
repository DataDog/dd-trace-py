// Note: some definitions are in TaintFuncs.h to avoid the problem of Python
// giving the "module not found in flat namespace" on import for templated
// functions.

// Needed for conversions from Vector to Tuple in get_ranges, dont remove even
// if CLion tells it's not used!
#include "string_utils.h"
#include "initializer/initializer.h"

using namespace pybind11::literals;

using namespace std;

// Used to quickly exit on cases where the object is a non interned unicode
// string and does not have the fast-taint mark on its internal data structure.
// In any other case it will return false so the evaluation continue for (more
// slowly) checking if bytes and bytearrays are tainted.
__attribute__((flatten)) bool
is_notinterned_notfasttainted_unicode(const PyObject* objptr)
{
    if (!objptr) {
        return true; // cannot taint a nullptr
    }

    if (!PyUnicode_Check(objptr)) {
        return false; // not a unicode, continue evaluation
    }

    if (PyUnicode_CHECK_INTERNED(objptr)) {
        return true; // interned but it could still be tainted
    }

    const _PyASCIIObject_State_Hidden* e = (_PyASCIIObject_State_Hidden*)&(((PyASCIIObject*)objptr)->state);
    if (!e) {
        return true; // broken string object? better to skip it
    }
    // it cannot be fast tainted if hash is set to -1 (not computed)
    Py_hash_t hash = ((PyASCIIObject*)objptr)->hash;
    return hash == -1 || e->hidden != GET_HASH_KEY(hash);
}

// For non interned unicode strings, set a hidden mark on it's internal data
// structure that will allow us to quickly check if the string is not tainted
// and thus skip further processing without having to search on the tainting map
__attribute__((flatten)) void
set_fast_tainted_if_notinterned_unicode(PyObject* objptr)
{
    if (not objptr or !PyUnicode_Check(objptr) or PyUnicode_CHECK_INTERNED(objptr)) {
        return;
    }
    if (auto e = (_PyASCIIObject_State_Hidden*)&(((PyASCIIObject*)objptr)->state)) {
        Py_hash_t hash = ((PyASCIIObject*)objptr)->hash;
        if (hash == -1) {
            hash = PyObject_Hash(objptr);
        }
        e->hidden = GET_HASH_KEY(hash);
    }
}

string
AnyTextObjectToString(PyObject* py_string_like)
{
    return AnyTextObjectToString(py::handle(py_string_like));
}

PyObject*
new_pyobject_id(PyObject* tainted_object)
{
    if (!tainted_object)
        return nullptr;

    // Check that it's aligned correctly
    if (reinterpret_cast<uintptr_t>(tainted_object) % alignof(PyObject) != 0)
        return tainted_object;

    // Try to safely access ob_type
    if (const PyObject* temp = tainted_object; !temp->ob_type)
        return tainted_object;

    py::gil_scoped_acquire acquire;

    if (PyUnicode_Check(tainted_object)) {
        PyObject* empty_unicode = PyUnicode_New(0, 127);
        if (!empty_unicode)
            return tainted_object;
        PyObject* val = Py_BuildValue("(OO)", tainted_object, empty_unicode);
        if (!val) {
            Py_XDECREF(empty_unicode);
            return tainted_object;
        }
        PyObject* result = PyUnicode_Join(empty_unicode, val);
        if (!result) {
            result = tainted_object;
        }
        Py_XDECREF(empty_unicode);
        Py_XDECREF(val);
        return result;
    }

    if (PyBytes_Check(tainted_object)) {
        PyObject* empty_bytes = PyBytes_FromString("");
        if (!empty_bytes)
            return tainted_object;

        const auto bytes_join_ptr = py::reinterpret_borrow<py::bytes>(empty_bytes).attr("join");
        const auto val = Py_BuildValue("(OO)", tainted_object, empty_bytes);
        if (!val or !bytes_join_ptr.ptr()) {
            Py_XDECREF(empty_bytes);
            return tainted_object;
        }

        const auto res = PyObject_CallFunctionObjArgs(bytes_join_ptr.ptr(), val, NULL);
        Py_XDECREF(val);
        Py_XDECREF(empty_bytes);
        if (res == nullptr) {
            return tainted_object;
        }
        return res;
    }

    if (PyByteArray_Check(tainted_object)) {
        PyObject* empty_bytes = PyBytes_FromString("");
        if (!empty_bytes)
            return tainted_object;

        PyObject* empty_bytearray = PyByteArray_FromObject(empty_bytes);
        if (!empty_bytearray) {
            Py_XDECREF(empty_bytes);
            return tainted_object;
        }

        const auto bytearray_join_ptr = py::reinterpret_borrow<py::bytes>(empty_bytearray).attr("join");
        const auto val = Py_BuildValue("(OO)", tainted_object, empty_bytearray);
        if (!val or !bytearray_join_ptr.ptr()) {
            Py_XDECREF(empty_bytes);
            Py_XDECREF(empty_bytearray);
            return tainted_object;
        }

        const auto res = PyObject_CallFunctionObjArgs(bytearray_join_ptr.ptr(), val, NULL);
        Py_XDECREF(val);
        Py_XDECREF(empty_bytes);
        Py_XDECREF(empty_bytearray);
        if (res == nullptr) {
            return tainted_object;
        }
        return res;
    }
    return tainted_object;
}

size_t
get_pyobject_size(PyObject* obj)
{
    size_t len_candidate_text{ 0 };
    if (PyUnicode_Check(obj)) {
        len_candidate_text = PyUnicode_GET_LENGTH(obj);
    } else if (PyBytes_Check(obj)) {
        len_candidate_text = PyBytes_Size(obj);
    } else if (PyByteArray_Check(obj)) {
        len_candidate_text = PyByteArray_Size(obj);
    }
    return len_candidate_text;
}

bool
PyIOBase_Check(const PyObject* obj)
{
    if (!obj)
        return false;

    try {
        return py::isinstance((PyObject*)obj, safe_import("_io", "_IOBase"));
    } catch (py::error_already_set& err) {
        PyErr_Clear();
        return false;
    }
}

bool
PyReMatch_Check(const PyObject* obj)
{
    if (!obj)
        return false;

    try {
        return py::isinstance((PyObject*)obj, safe_import("re", "Match"));
    } catch (py::error_already_set& err) {
        PyErr_Clear();
        return false;
    }
}
