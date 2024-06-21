#include "helpers.h"

static PyObject*
cause_segfault(PyObject* self, PyObject* args)
{
    int p = *(int*)NULL;
    return PyLong_FromLong(0);
}

static PyMethodDef Methods[] = { { "cause_segfault", cause_segfault, METH_VARARGS, "Causes a segfault" },
                                 { NULL, NULL, 0, NULL } };

static struct PyModuleDef Module = { PyModuleDef_HEAD_INIT, MOD_STR(LIB_NAME), NULL, -1, Methods };

PyMODINIT_FUNC
PYINIT_NAME(LIB_NAME)(void)
{
    return PyModule_Create(&Module);
}
