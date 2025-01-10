#define PY_SSIZE_T_CLEAN
#include <Python.h>

#if PY_VERSION_HEX < 0x030c0000
#if defined __GNUC__ && defined HAVE_STD_ATOMIC
#undef HAVE_STD_ATOMIC
#endif
#endif

// ----------------------------------------------------------------------------
static PyObject*
replace_in_tuple(PyObject* m, PyObject* args)
{
    PyObject* tuple = NULL;
    PyObject* item = NULL;
    PyObject* replacement = NULL;

    if (!PyArg_ParseTuple(args, "O!OO", &PyTuple_Type, &tuple, &item, &replacement))
        return NULL;

    for (Py_ssize_t i = 0; i < PyTuple_Size(tuple); i++) {
        PyObject* current = PyTuple_GetItem(tuple, i);
        if (current == item) {
            Py_DECREF(current);
            // !!! DANGER !!!
            PyTuple_SET_ITEM(tuple, i, replacement);
            Py_INCREF(replacement);
        }
    }

    Py_RETURN_NONE;
}

// ----------------------------------------------------------------------------
static PyMethodDef native_methods[] = {
    { "replace_in_tuple", replace_in_tuple, METH_VARARGS, "Replace an item in a tuple." },
    { NULL, NULL, 0, NULL } /* Sentinel */
};

// ----------------------------------------------------------------------------
static struct PyModuleDef nativemodule = {
    PyModuleDef_HEAD_INIT,
    "_native", /* name of module */
    NULL,      /* module documentation, may be NULL */
    -1,        /* size of per-interpreter state of the module,
                  or -1 if the module keeps state in global variables. */
    native_methods,
};

// ----------------------------------------------------------------------------
PyMODINIT_FUNC
PyInit__native(void)
{
    PyObject* m;

    m = PyModule_Create(&nativemodule);
    if (m == NULL)
        return NULL;

    return m;
}
