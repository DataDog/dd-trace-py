#include "cast_to_pyfunc.hpp"
#include "python_headers.hpp"
#include "sampler.hpp"

#include <mutex>
#include <unordered_map>

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

// Echion needs us to propagate information about threads, usually at thread start by patching the threading module
// We reference some data structures here which are internal to echion (but global in scope)
static PyObject*
stack_v2_thread_register(PyObject* self, PyObject* args)
{

    (void)self;

    uintptr_t id;
    uint64_t native_id;
    const char* name;

    if (!PyArg_ParseTuple(args, "KKs", &id, &native_id, &name)) {
        return NULL;
    }

    Sampler::get().register_thread(id, native_id, name);
    Py_RETURN_NONE;
}

static PyObject*
stack_v2_thread_unregister(PyObject* self, PyObject* args)
{
    (void)self;
    uint64_t id;

    if (!PyArg_ParseTuple(args, "K", &id)) {
        return NULL;
    }

    Sampler::get().unregister_thread(id);
    Py_RETURN_NONE;
}

static PyMethodDef _stack_v2_methods[] = {
    { "start", reinterpret_cast<PyCFunction>(stack_v2_start), METH_VARARGS | METH_KEYWORDS, "Start the sampler" },
    { "stop", stack_v2_stop, METH_VARARGS, "Stop the sampler" },
    { "register_thread", stack_v2_thread_register, METH_VARARGS, "Register a thread" },
    { "unregister_thread", stack_v2_thread_unregister, METH_VARARGS, "Unregister a thread" },
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
