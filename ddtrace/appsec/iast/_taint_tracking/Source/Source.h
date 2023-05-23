#ifndef _TAINT_TRACKING_SOURCE_H
#define _TAINT_TRACKING_SOURCE_H
#include <pybind11/pybind11.h>
#include <sstream>
#include <Python.h>
#include <string.h>
#include <iostream>
#include "structmember.h"

#include "../Constants.h"

// #define PY_MODULE_NAME_SOURCE PY_MODULE_NAME "." "Source"
using namespace std;
namespace py = pybind11;

enum class OriginType {
    PARAMETER = 0,
    PARAMETER_NAME,
    HEADER,
    HEADER_NAME,
    PATH,
    BODY,
    QUERY,
    PATH_PARAMETER,
    COOKIE,
    COOKIE_NAME
};


struct Source {
    Source(string, string, OriginType);
    Source() = default;
    string name;
    string value;
    OriginType origin;
    int refcount = 0;

    [[nodiscard]] string toString() const;

    inline void set_values(string name, string value, OriginType origin ) {
        this->name = move(name);
        this->value = move(value);
        this->origin = origin;
    }

    [[nodiscard]] size_t get_hash() const;

    static inline size_t hash(const string& name, const OriginType origin, const string& value) {
        return std::hash<size_t>()(std::hash<string>()(name) ^ (long) origin ^ std::hash<string>()(value));
    };

    explicit operator std::string() const;
    bool eq(Source* other) const;
};

using SourcePtr = Source*;

inline string origin_to_str(OriginType origin_type) {
    switch (origin_type) {
        case OriginType::PARAMETER:
            return "http.request.parameter";
        case OriginType::PARAMETER_NAME:
            return "http.request.parameter.name";
        case OriginType::HEADER:
            return "http.request.header";
        case OriginType::HEADER_NAME:
            return "http.request.header.name";
        case OriginType::PATH:
            return "http.request.path";
        case OriginType::BODY:
            return "http.request.body";
        case OriginType::QUERY:
            return "http.request.query";
        case OriginType::PATH_PARAMETER:
            return "http.request.path.parameter";
        case OriginType::COOKIE_NAME:
            return "http.request.cookie.name";
        case OriginType::COOKIE:
            return "http.request.cookie.value";
        default:
            return "";
    }
}

void pyexport_source(py::module& m);

//static void
//Source_dealloc(Source *self)
//{
//    // JJJ: free the members?
//    Py_TYPE(self)->tp_free((PyObject *) self);
//}
//
//
//static PyObject *
//Source_new(PyTypeObject *type, PyObject *args, PyObject *kwds)
//{
//    Source *self;
//    self = (Source *) type->tp_alloc(type, 0);
//    if (self != nullptr) {
//        self->name = "";
//        self->value = "";
//        self->origin = "";
//    }
//    return (PyObject *) self;
//}
//
//static int
//Source_init(Source *self, PyObject *args, PyObject *kwds)
//{
//    static char *kwlist[] = {"name", "value", "origin", nullptr};
//
//    char *name = nullptr;
//    char *value = nullptr;
//    char *origin = nullptr;
//    if (!PyArg_ParseTupleAndKeywords(args, kwds, "|sss", kwlist, &name, &value, &origin))
//        return -1;
//
//    if (name) self->name = strdup(name);
//    if (value) self->value = strdup(value);
//    if (origin) self->origin = strdup(origin);
//    return 0;
//}
//
//static PyObject *
//Source_to_string(Source *self, PyObject *Py_UNUSED(ignored))
//{
//  return PyUnicode_FromFormat("%s", self->toString().c_str());
//}
//
//static PyMemberDef Source_members[] = {
//        {"name", T_STRING, offsetof(Source, name), 0,
//                "Source.name"},
//        {"value", T_STRING, offsetof(Source, value), 0,
//                "Source.value"},
//        {"origin", T_STRING, offsetof(Source, origin), 0,
//                "Source.origin"},
//        {nullptr}  /* Sentinel */
//};
//
//static PyObject *Source_equals(Source *self, PyObject *args);
//static PyMethodDef Source_methods[] = {
//        {"to_string", (PyCFunction) Source_to_string, METH_NOARGS,
//                "Return representation of a Source"
//        },
//        {nullptr, nullptr, 0, nullptr}  /* Sentinel */
//};
//
//static PyObject *
//Source_richcompare(PyObject *self, PyObject *other, int op)
//{
//    PyObject *result = NULL;
//
//    switch (op) {
//        case Py_EQ:
//            result = reinterpret_cast<Source*>(self)->eq(reinterpret_cast<Source*>(other)) ? Py_True : Py_False;
//            break;
//        default:
//            result = Py_NotImplemented;
//            break;
//    }
//
//    Py_XINCREF(result);
//}
//
//static PyObject *
//Source_repr(PyObject *self)
//{
//    return Source_to_string(reinterpret_cast<Source*>(self), nullptr);
//}
//
//static PyTypeObject SourceType = {
//        PyVarObject_HEAD_INIT(nullptr, 0)
//        .tp_name = PY_MODULE_NAME_SOURCE,
//        .tp_basicsize = sizeof(Source),
//        .tp_itemsize = 0,
//        .tp_dealloc = (destructor) Source_dealloc,
//        .tp_repr = Source_repr,
//        .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE,
//        .tp_doc = PyDoc_STR("Source objects"),
//        .tp_richcompare = Source_richcompare,
//        .tp_methods = Source_methods,
//        .tp_members = Source_members,
//        .tp_init = (initproc) Source_init,
//        .tp_new = Source_new,
//};

#endif //_TAINT_TRACKING_SOURCE_H
