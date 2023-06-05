#include "TaintedObject.h"

PyObject* bytes_join = NULL;
PyObject* bytearray_join = NULL;
PyObject* empty_bytes = NULL;
PyObject* empty_bytearray = NULL;
PyObject* empty_unicode = NULL;

PyObject*
setup(PyObject* Py_UNUSED(module), PyObject* args)
{
    PyArg_ParseTuple(args, "OO", &bytes_join, &bytearray_join);
    empty_bytes = PyBytes_FromString("");
    empty_bytearray = PyByteArray_FromObject(empty_bytes);
    empty_unicode = PyUnicode_New(0, 127);
    Py_RETURN_NONE;
}

PyObject*
new_pyobject_id(PyObject* Py_UNUSED(module), PyObject* args)
{
    PyObject* tainted_object;
    Py_ssize_t object_length;
    PyArg_ParseTuple(args, "On", &tainted_object, &object_length);
    if (PyUnicode_Check(tainted_object)) {
        if (PyUnicode_CHECK_INTERNED(tainted_object) == 0) { // SSTATE_NOT_INTERNED
            Py_INCREF(tainted_object);
            return tainted_object;
        }
        return PyUnicode_Join(empty_unicode, Py_BuildValue("(OO)", tainted_object, empty_unicode));
    } else if (object_length > 1) {
        // Bytes and bytearrays with length > 1 are not interned
        Py_INCREF(tainted_object);
        return tainted_object;
    } else if (PyBytes_Check(tainted_object)) {
        return PyObject_CallFunctionObjArgs(
          bytes_join, empty_bytes, Py_BuildValue("(OO)", tainted_object, empty_bytes), NULL);
    } else {
        return PyObject_CallFunctionObjArgs(
          bytearray_join, empty_bytearray, Py_BuildValue("(OO)", tainted_object, empty_bytearray), NULL);
    }
}