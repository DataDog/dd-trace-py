#include "TaintedObject/TaintedObject.h"

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
