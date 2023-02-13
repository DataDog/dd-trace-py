#include <Python.h>
#include <unordered_set>

/* def taint_pyobject(PyObject: py_object): -> None */
/*     GLOBAL TAINT_DICT */
/*     TAINT_DICT[py_object.__id__] = True */

/* def is_pyobject_tainted(PyObject: py_object): -> Bool */
/*     return TAINT_DICT[py_object.__id__] or False */

std::unordered_set<PyObject*> TaintMapping{};

static PyObject*
clear_taint_mapping(PyObject* Py_UNUSED(module), PyObject* Py_UNUSED(args))
{
    TaintMapping.clear();
    Py_RETURN_NONE;
}

static PyObject*
taint_pyobject(PyObject* Py_UNUSED(module), PyObject* py_object)
{
    TaintMapping.insert(py_object);
    Py_RETURN_NONE;
}

static PyObject*
is_pyobject_tainted(PyObject* Py_UNUSED(module), PyObject* py_object)
{
    if (TaintMapping.count(py_object))
        Py_RETURN_TRUE;
    Py_RETURN_FALSE;
}


static PyMethodDef TaintTrackingMethods[] = {
    { "clear_taint_mapping", (PyCFunction)clear_taint_mapping, METH_NOARGS, "clear taint mappings" },
    { "taint_pyobject", (PyCFunction)taint_pyobject, METH_O, "taint pyobject" },
    { "is_pyobject_tainted", (PyCFunction)is_pyobject_tainted, METH_O, "is pyobject tainted" },
    { NULL, NULL, 0, NULL }
};

static struct PyModuleDef taint_tracking = { PyModuleDef_HEAD_INIT,
                                         "ddtrace.appsec.iast._taint_tracking",
                                         "taint tracking module",
                                         -1,
                                         TaintTrackingMethods };

PyMODINIT_FUNC
PyInit__taint_tracking(void)
{
    PyObject* m;
    m = PyModule_Create(&taint_tracking);
    if (m == NULL)
        return NULL;
    return m;
}
