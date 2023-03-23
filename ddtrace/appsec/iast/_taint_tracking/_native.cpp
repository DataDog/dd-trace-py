#include <Python.h>
#include <iostream>
#include <tuple>

PyObject *bytes_join = NULL;
PyObject *bytearray_join = NULL;
PyObject *empty_bytes = NULL;
PyObject *empty_bytearray = NULL;
PyObject *empty_unicode = NULL;

static PyObject *setup(PyObject *Py_UNUSED(module), PyObject *args) {
  PyArg_ParseTuple(args, "OO", &bytes_join, &bytearray_join);
  empty_bytes = PyBytes_FromString("");
  empty_bytearray = PyByteArray_FromObject(empty_bytes);
  empty_unicode = PyUnicode_New(0, 127);
  Py_RETURN_NONE;
}

static PyObject *new_pyobject_id(PyObject *Py_UNUSED(module), PyObject *args) {
  PyObject *tainted_object;
  Py_ssize_t object_length;
  PyArg_ParseTuple(args, "On", &tainted_object, &object_length);
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

static PyMethodDef TaintTrackingMethods[] = {
    // We are using  METH_VARARGS because we need compatibility with
    // python 3.5, 3.6. but METH_FASTCALL could be used instead for python
    // >= 3.7
    {"setup", (PyCFunction)setup, METH_VARARGS, "setup tainting module"},
    {"new_pyobject_id", (PyCFunction)new_pyobject_id, METH_VARARGS,
     "new_pyobject_id"},
    {NULL, NULL, 0, NULL}};

static struct PyModuleDef taint_tracking = {
    PyModuleDef_HEAD_INIT, "ddtrace.appsec.iast._taint_tracking._native",
    "taint tracking module", -1, TaintTrackingMethods};

PyMODINIT_FUNC PyInit__native(void) {
  PyObject *m;
  m = PyModule_Create(&taint_tracking);
  if (m == NULL)
    return NULL;
  return m;
}
