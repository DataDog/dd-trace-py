#include "helpers.h"

static PyObject* example_add(PyObject* self, PyObject* args) {
    double a, b;
    if (!PyArg_ParseTuple(args, "dd", &a, &b)) {
        return NULL;
    }
    return PyFloat_FromDouble(a + b); // Return the sum
}

static PyMethodDef ExampleMethods[] = {
    {"add", example_add, METH_VARARGS, "Add two numbers."},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef examplemodule = {
    PyModuleDef_HEAD_INIT,
    MOD_STR(LIB_NAME),
    NULL,
    -1,
    ExampleMethods
};

PyMODINIT_FUNC PYINIT_NAME(LIB_NAME)(void) {
    return PyModule_Create(&examplemodule);
}
