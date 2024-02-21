define PY_SSIZE_T_CLEAN
#include <Python.h>

#if PY_VERSION_HEX >= 0x030c0000
// https://github.com/python/cpython/issues/108216#issuecomment-1696565797
#undef _PyGC_FINALIZED
#endif

#include "sampler.hpp"

  static PyObject*
  stack_v2_start(PyObject* self, PyObject* args)
{
    static const char* const_kwlist[] = { "min_interval", "max_frames", NULL };
    static char** kwlist = const_cast<char**>(const_kwlist);
    double min_interval_s = g_default_sampling_period_s;
    double max_nframes = g_default_max_nframes;

    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|dd", kwlist, &min_interval_f, &max_frames)) {
        return NULL; // If an error occurs during argument parsing
    }

    Sampler::get().set_interval(min_interval_s);
    Sampler::get().set_max_nframes(max_nframes);
    Sampler::init(min_interval_s, max_frames);

    return PyLong_FromLong(1);
}

static PyObject*
set_v2_interval(PyObject* self, PyObject* args)
{
    double new_interval;
    if (!PyArg_ParseTuple(args, "d", &new_interval)) {
        return NULL; // If an error occurs during argument parsing
    }
    _set_v2_interval(new_interval);
    Py_INCREF(Py_None);
    return Py_None;
}

static PyMethodDef _stack_v2_methods[] = {
    { "start", (PyCFunction)start_stack_v2, METH_VARARGS | METH_KEYWORDS, "Start the sampler" },
    { "stop", (PyCFunction)stop_stack_v2, METH_VARARGS, "Stop the sampler" },
    { "set_interval", (PyCFunction)set_v2_interval, METH_VARARGS, "Set the sampling interval" },
    { NULL, NULL, 0, NULL }
};

PyMODINIT_FUNC
PyInit_stack_v2(void)
{
    PyObject* m;
    static struct PyModuleDef moduledef = {
        PyModuleDef_HEAD_INIT, "_stack_v2", NULL, -1, _stack_v2_methods, NULL, NULL, NULL, NULL
    };

    m = PyModule_Create(&moduledef);
    if (!m)
        return NULL;

    return m;
}
