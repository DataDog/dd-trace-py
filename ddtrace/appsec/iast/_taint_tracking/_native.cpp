#include <pybind11/pybind11.h>

#include "Constants.h"
#include "Source/Source.h"
#include "TaintRange/TaintRange.h"
#include "TaintedObject/TaintedObject.h"
#include "TaintedOps/TaintedOps.h"
#include "Aspects/AspectOperatorAdd.h"

#define PY_MODULE_NAME_ASPECTS PY_MODULE_NAME "." "aspects"

using namespace pybind11::literals;
namespace py = pybind11;

static PyMethodDef AspectsMethods[] = {
    // We are using  METH_VARARGS because we need compatibility with
    // python 3.5, 3.6. but METH_FASTCALL could be used instead for python
    // >= 3.7
    //{"setup", (PyCFunction)setup, METH_VARARGS, "setup tainting module"},
    //{"new_pyobject_id", (PyCFunction)api_new_pyobject_id, METH_VARARGS,"new pyobject id"},
    {"add_aspect", ((PyCFunction) api_add_aspect), METH_FASTCALL, "aspect add"},
    {nullptr, nullptr, 0, nullptr}};

static struct PyModuleDef aspects = {
    PyModuleDef_HEAD_INIT,
    .m_name = PY_MODULE_NAME_ASPECTS,
    .m_doc = "Taint tracking Aspects.",
    .m_size = -1,
    .m_methods = AspectsMethods};

PYBIND11_MODULE(_native, m) {
    // Cleanup code to be run at the end of the interpreter lifetime:

    m.doc() = "Native Python module";

    // Note: the order of these definitions matter. For example,
    // stacktrace_element definitions must be before the ones of the
    // classes inheriting from it.
    m.def("setup", &setup, "A function that adds two numbers");
    PyObject* hm = PyModule_Create(&aspects);
    m.add_object("aspects", hm);
}
