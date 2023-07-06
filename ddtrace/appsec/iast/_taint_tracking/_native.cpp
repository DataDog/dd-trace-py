#include <pybind11/pybind11.h>

#include "TaintedObject/TaintedObject.h"

using namespace pybind11::literals;
namespace py = pybind11;

static PyMethodDef OpsMethods[] = {
    // We are using  METH_VARARGS because we need compatibility with
    // python 3.5, 3.6. but METH_FASTCALL could be used instead for python
    // >= 3.7
    { "setup", (PyCFunction)setup, METH_VARARGS, "setup tainting module" },
    { "new_pyobject_id", (PyCFunction)new_pyobject_id, METH_VARARGS, "new pyobject id" },
    { nullptr, nullptr, 0, nullptr }
};

static struct PyModuleDef ops = { PyModuleDef_HEAD_INIT,
                                  .m_name = "ddtrace.appsec.iast._taint_tracking._native",
                                  .m_doc = "Taint tracking operations",
                                  .m_size = -1,
                                  .m_methods = OpsMethods };

PYBIND11_MODULE(_native, m)
{
    // Note: the order of these definitions matter. For example,
    // stacktrace_element definitions must be before the ones of the
    // classes inheriting from it.
    PyObject* hm_ops = PyModule_Create(&ops);
    m.add_object("ops", hm_ops);
}