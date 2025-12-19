#include <Python.h>
#include <limits.h>

static PyObject*
overflow(PyObject* self, PyObject* args)
{
    volatile int x = INT_MAX;
    int y = x + 1; // signed integer overflow — undefined
    return PyLong_FromLong((long)y);
}

static PyMethodDef M[] = { { "overflow", overflow, METH_NOARGS, NULL }, { NULL, NULL, 0, NULL } };

static struct PyModuleDef moddef = { PyModuleDef_HEAD_INIT, "ubsantest", NULL, -1, M };

PyMODINIT_FUNC
PyInit_ubsantest(void)
{
    return PyModule_Create(&moddef);
}
