//
// Created by alberto.vara on 16/05/23.
//

#ifndef _TAINT_TRACKING_SOURCE_H
#define _TAINT_TRACKING_SOURCE_H
#include <sstream>
#include <Python.h>
#include "structmember.h"
#include "../Constants.h"

#define PY_MODULE_NAME_SOURCE PY_MODULE_NAME "." "Source"


using namespace std;

struct Source {
    string name;
    string value;
    // TODO: make origin an enum
    string origin;

    [[nodiscard]] const char* toString() const;

    [[nodiscard]] size_t get_hash() const;

    struct hash_fn {
        size_t operator()(const Source& source) const { return source.get_hash(); }
    };

    [[nodiscard]] size_t hash_() const { return hash_fn()(*this); }

    explicit operator std::string() const;
};


static void
Source_dealloc(Source *self)
{
    Py_TYPE(self)->tp_free((PyObject *) self);
}


static PyObject *
Source_new(PyTypeObject *type, PyObject *args, PyObject *kwds)
{
    Source *self;
    self = (Source *) type->tp_alloc(type, 0);
    if (self != NULL) {
        self->name = "";
        self->value = "";
        self->origin = "";
    }
    return (PyObject *) self;
}

static int
Source_init(Source *self, PyObject *args, PyObject *kwds)
{
    static char *kwlist[] = {"name", "value", "origin", NULL};
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "|sss", kwlist,
                                     &self->name, &self->value, &self->origin))
        return -1;
    return 0;
}

static PyObject *
Source_to_string(Source *self, PyObject *Py_UNUSED(ignored))
{
    return PyUnicode_FromFormat("%S", self->toString());
}


static PyMemberDef Source_members[] = {
        {"name", T_OBJECT_EX, offsetof(Source, name), 0,
                "Source.name"},
        {"value", T_OBJECT_EX, offsetof(Source, value), 0,
                "Source.value"},
        {"origin", T_OBJECT_EX, offsetof(Source, origin), 0,
                "Source.origin"},
        {NULL}  /* Sentinel */
};

static PyMethodDef Source_methods[] = {
        {"to_string", (PyCFunction) Source_to_string, METH_NOARGS,
                "Return representation of a Source"
        },
        {NULL}  /* Sentinel */
};

static PyTypeObject SourceType = {
        PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = PY_MODULE_NAME_SOURCE,
        .tp_basicsize = sizeof(Source),
        .tp_itemsize = 0,
        .tp_dealloc = (destructor) Source_dealloc,
        .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE,
        .tp_doc = PyDoc_STR("Source objects"),
        .tp_methods = Source_methods,
        .tp_members = Source_members,
        .tp_init = (initproc) Source_init,
        .tp_new = Source_new,
};

#endif //_TAINT_TRACKING_SOURCE_H
