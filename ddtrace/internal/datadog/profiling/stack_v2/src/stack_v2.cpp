#include "cast_to_pyfunc.hpp"
#include "python_headers.hpp"
#include "sampler.hpp"

using namespace Datadog;

static PyObject*
_stack_v2_start(PyObject* self, PyObject* args, PyObject* kwargs)
{
    (void)self;
    static const char* const_kwlist[] = { "min_interval", NULL };
    static char** kwlist = const_cast<char**>(const_kwlist);
    double min_interval_s = g_default_sampling_period_s;

    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|d", kwlist, &min_interval_s)) {
        return NULL; // If an error occurs during argument parsing
    }

    Sampler::get().set_interval(min_interval_s);
    Sampler::get().start();
    Py_RETURN_NONE;
}

// Bypasses the old-style cast warning with an unchecked helper function
PyCFunction stack_v2_start = cast_to_pycfunction(_stack_v2_start);

static PyObject*
stack_v2_stop(PyObject* self, PyObject* args)
{
    (void)self;
    (void)args;
    Sampler::get().stop();
    Py_RETURN_NONE;
}

static PyObject*
stack_v2_set_interval(PyObject* self, PyObject* args)
{
    // Assumes the interval is given in fractional seconds
    (void)self;
    double new_interval;
    if (!PyArg_ParseTuple(args, "d", &new_interval)) {
        return NULL; // If an error occurs during argument parsing
    }
    Sampler::get().set_interval(new_interval);
    Py_RETURN_NONE;
}

static PyMethodDef _stack_v2_methods[] = {
    { "start", reinterpret_cast<PyCFunction>(stack_v2_start), METH_VARARGS | METH_KEYWORDS, "Start the sampler" },
    { "stop", stack_v2_stop, METH_VARARGS, "Stop the sampler" },
    { "set_interval", stack_v2_set_interval, METH_VARARGS, "Set the sampling interval" },
    { NULL, NULL, 0, NULL }
};

PyMODINIT_FUNC
PyInit__stack_v2(void)
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
