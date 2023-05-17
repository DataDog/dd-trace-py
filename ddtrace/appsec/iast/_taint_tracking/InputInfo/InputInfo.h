//
// Created by alberto.vara on 16/05/23.
//

#ifndef _TAINT_TRACKING_INPUTINFO_H
#define _TAINT_TRACKING_INPUTINFO_H
#include <sstream>
#include <Python.h>
#include "structmember.h"
#include "../Constants.h"

#define PY_MODULE_NAME_INPUTINFO PY_MODULE_NAME "." "InputInfo"


using namespace std;

struct InputInfo {
    string name;
    string value;
    string origin;

    [[nodiscard]] const char* toString() const;

    [[nodiscard]] size_t get_hash() const;

    struct hash_fn {
        size_t operator()(const InputInfo& inputinfo) const { return inputinfo.get_hash(); }
    };

    [[nodiscard]] size_t hash_() const { return hash_fn()(*this); }

    explicit operator std::string() const;
};


static void
InputInfo_dealloc(InputInfo *self)
{
    Py_TYPE(self)->tp_free((PyObject *) self);
}


static PyObject *
InputInfo_new(PyTypeObject *type, PyObject *args, PyObject *kwds)
{
    InputInfo *self;
    self = (InputInfo *) type->tp_alloc(type, 0);
    if (self != NULL) {
        self->name = "";
        self->value = "";
        self->origin = "";
    }
    return (PyObject *) self;
}

static int
InputInfo_init(InputInfo *self, PyObject *args, PyObject *kwds)
{
    static char *kwlist[] = {"name", "value", "origin", NULL};
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "|sss", kwlist,
                                     &self->name, &self->value, &self->origin))
        return -1;
    return 0;
}

static PyObject *
InputInfo_to_string(InputInfo *self, PyObject *Py_UNUSED(ignored))
{
    return PyUnicode_FromFormat("%S", self->toString());
}


static PyMemberDef InputInfo_members[] = {
        {"name", T_OBJECT_EX, offsetof(InputInfo, name), 0,
                "InputInfo.name"},
        {"value", T_OBJECT_EX, offsetof(InputInfo, value), 0,
                "InputInfo.value"},
        {"origin", T_OBJECT_EX, offsetof(InputInfo, origin), 0,
                "InputInfo.origin"},
        {NULL}  /* Sentinel */
};

static PyMethodDef InputInfo_methods[] = {
        {"to_string", (PyCFunction) InputInfo_to_string, METH_NOARGS,
                "Return representation of a InputInfo"
        },
        {NULL}  /* Sentinel */
};

static PyTypeObject InputInfoType = {
        PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = PY_MODULE_NAME_INPUTINFO,
        .tp_basicsize = sizeof(InputInfo),
        .tp_itemsize = 0,
        .tp_dealloc = (destructor) InputInfo_dealloc,
        .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE,
        .tp_doc = PyDoc_STR("InputInfo objects"),
        .tp_methods = InputInfo_methods,
        .tp_members = InputInfo_members,
        .tp_init = (initproc) InputInfo_init,
        .tp_new = InputInfo_new,
};

#endif //_TAINT_TRACKING_INPUTINFO_H
