#include "Constants.h"
#include "InputInfo/InputInfo.h"
#include "TaintRange/TaintRange.h"
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
    PyModuleDef_HEAD_INIT, .m_name = PY_MODULE_NAME,
    .m_doc = "Taint tracking module.", .m_size = -1,
    .m_methods = TaintTrackingMethods};

PyMODINIT_FUNC PyInit__native(void) {
  PyObject *m;

  if (PyType_Ready(&TaintRangeType) < 0)
    return NULL;

  m = PyModule_Create(&taint_tracking);
  if (m == NULL)
    return NULL;

  Py_INCREF(&InputInfoType);
  if (PyModule_AddObject(m, "InputInfo", (PyObject *)&InputInfoType) < 0) {
    Py_DECREF(&InputInfoType);
    Py_DECREF(m);
    return NULL;
  }

  Py_INCREF(&TaintRangeType);
  if (PyModule_AddObject(m, "TaintRange", (PyObject *)&TaintRangeType) < 0) {
    Py_DECREF(&TaintRangeType);
    Py_DECREF(m);
    return NULL;
  }

  return m;
}
