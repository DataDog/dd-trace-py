#include "TaintedOps.h"

PyObject *bytes_join = NULL;
PyObject *bytearray_join = NULL;
PyObject *empty_bytes = NULL;
PyObject *empty_bytearray = NULL;
PyObject *empty_unicode = NULL;

typedef struct _PyASCIIObject_State_Hidden {
    unsigned int : 8;
    unsigned int hidden : 24;
} PyASCIIObject_State_Hidden;


PyObject *setup(PyObject *Py_UNUSED(module), PyObject *args) {
  PyArg_ParseTuple(args, "OO", &bytes_join, &bytearray_join);
  empty_bytes = PyBytes_FromString("");
  empty_bytearray = PyByteArray_FromObject(empty_bytes);
  empty_unicode = PyUnicode_New(0, 127);
  Py_RETURN_NONE;
}

TaintedObject* get_tainted_object(const PyObject* str, TaintRangeMapType* tx_taint_map) {
    if (!could_be_tainted(str) or !tx_taint_map or tx_taint_map->empty()) {
        return nullptr;
    }

    auto it = tx_taint_map->find(get_unique_id(str));
    return it == tx_taint_map->end() ? nullptr : it->second;
}

PyObject *new_pyobject_id(PyObject *tainted_object, Py_ssize_t object_length) {
    if (PyUnicode_Check(tainted_object)) {
    if (PyUnicode_CHECK_INTERNED(tainted_object) == 0) { // SSTATE_NOT_INTERNED
    Py_INCREF(tainted_object);
    return tainted_object;
    }
    return PyUnicode_Join(empty_unicode,
            Py_BuildValue("(OO)", tainted_object, empty_unicode));
    } else if (object_length > 1) {
    // Bytes and bytearrays with length > 1 are not interned
    Py_INCREF(tainted_object);
    return tainted_object;
    } else if (PyBytes_Check(tainted_object)) {
    return PyObject_CallFunctionObjArgs(
            bytes_join, empty_bytes,
            Py_BuildValue("(OO)", tainted_object, empty_bytes), NULL);
    } else {
    return PyObject_CallFunctionObjArgs(
            bytearray_join, empty_bytearray,
            Py_BuildValue("(OO)", tainted_object, empty_bytearray), NULL);
    }
}

PyObject *api_new_pyobject_id(PyObject *Py_UNUSED(module), PyObject *args) {
  PyObject *tainted_object;
  Py_ssize_t object_length;
  PyArg_ParseTuple(args, "On", &tainted_object, &object_length);
  return new_pyobject_id(tainted_object, object_length);
}

__attribute__((flatten)) bool could_be_tainted(const PyObject* op) {
    if (op == nullptr) {
        return false;
    }
    if (!PyUnicode_Check(op)) {
        return true;
    }
    if (PyUnicode_CHECK_INTERNED(op) != SSTATE_NOT_INTERNED) {
        return false;
    }
    const PyASCIIObject_State_Hidden* e = (PyASCIIObject_State_Hidden*) &(((PyASCIIObject*) op)->state);
    return e->hidden == 1;
}


__attribute__((flatten)) void set_could_be_tainted(PyObject* op) {
    if (op == nullptr) {
        return;
    }
    if (!PyUnicode_Check(op)) {
        return;
    }
    if (PyUnicode_CHECK_INTERNED(op) != SSTATE_NOT_INTERNED) {
        return;
    }
    PyASCIIObject_State_Hidden* e = (PyASCIIObject_State_Hidden*) &(((PyASCIIObject*) op)->state);
    e->hidden = 1;
}