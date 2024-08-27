// Note: some definitions are in TaintFuncs.h to avoid the problem of Python
// giving the "module not found in flat namespace" on import for templated
// functions.

// Needed for conversions from Vector to Tuple in get_ranges, dont remove even
// if CLion tells it's not used!
#include "StringUtils.h"

using namespace pybind11::literals;

using namespace std;

#define GET_HASH_KEY(hash) (hash & 0xFFFFFF)

typedef struct _PyASCIIObject_State_Hidden
{
    unsigned int : 8;
    unsigned int hidden : 24;
} PyASCIIObject_State_Hidden;

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

// For non interned unicode strings, set a hidden mark on it's internsal data
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
PyObjectToString(PyObject* obj)
{
    const char* str = PyUnicode_AsUTF8(obj);

    if (str == nullptr) {
        return "";
    }
    return str;
}

PyObject*
new_pyobject_id(PyObject* tainted_object)
{
    if (!tainted_object)
        return nullptr;

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
        return res;
    } else if (PyByteArray_Check(tainted_object)) {
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