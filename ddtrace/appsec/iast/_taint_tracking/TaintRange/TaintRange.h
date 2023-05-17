//
// Created by alberto.vara on 16/05/23.
//

#ifndef _TAINT_TRACKING_TAINTRANGE_H
#define _TAINT_TRACKING_TAINTRANGE_H
#include <Python.h>
#include "structmember.h"

#include "../Constants.h"
#include "../InputInfo/InputInfo.h"

#define PY_MODULE_NAME_TAINTRANGES PY_MODULE_NAME "." "TaintRange"


struct TaintRange {
    PyObject_HEAD
    long position{};
    long length{};
    InputInfo inputinfo;

    TaintRange() = default;

    TaintRange(long position, long length, InputInfo inputinfo)
            : position(position),
              length(length),
              inputinfo(std::move(inputinfo)){}

    void reset();

    [[nodiscard]] const char* toString() const;

    [[nodiscard]] size_t get_hash() const;

    struct hash_fn {
        size_t operator()(const TaintRange &range) const { return range.get_hash(); }
    };

    [[nodiscard]] size_t hash_() const { return hash_fn()(*this); }

    explicit operator std::string() const;
};


static void
TaintRange_dealloc(TaintRange *self)
{
    Py_XDECREF(self->position);
    Py_XDECREF(self->length);
    // TODO: dealloc inputinfo
    //  Py_XDECREF(self->inputinfo);
    Py_TYPE(self)->tp_free((PyObject *) self);
}


static PyObject *
TaintRange_new(PyTypeObject *type, PyObject *args, PyObject *kwds)
{
    TaintRange *self;
    self = (TaintRange *) type->tp_alloc(type, 0);
    if (self != NULL) {
//        self->inputinfo = InputInfo_new();
//        if (self->inputinfo == NULL) {
//            Py_DECREF(self);
//            return NULL;
//        }

        self->position = 0;
        self->length = 0;
    }
    return (PyObject *) self;
}

static int
TaintRange_init(TaintRange *self, PyObject *args, PyObject *kwds)
{
    static char *kwlist[] = {"position", "length", "inputinfo", NULL};
    PyObject *inputinfo = NULL, *tmp;

    if (!PyArg_ParseTupleAndKeywords(args, kwds, "|iiO", kwlist,
                                     &self->position, &self->length,
                                     &inputinfo))
        return -1;

    if (inputinfo) {
        // TODO: assign input info
//        tmp = self->inputinfo;
//        Py_INCREF(inputinfo);
//        self->inputinfo = inputinfo;
        Py_XDECREF(tmp);
    }

    return 0;
}

static PyObject *
TaintRange_to_string(TaintRange *self, PyObject *Py_UNUSED(ignored))
{
    if (self->position == NULL) {
        PyErr_SetString(PyExc_AttributeError, "position");
        return NULL;
    }
    if (self->length == NULL) {
        PyErr_SetString(PyExc_AttributeError, "length");
        return NULL;
    }
    return PyUnicode_FromFormat("%S", self->toString());
}


static PyMemberDef TaintRange_members[] = {
        {"length", T_INT, offsetof(TaintRange, length), 0,
                "TaintRange last name"},
        {"position", T_INT, offsetof(TaintRange, position), 0,
                "TaintRange position"},
        {NULL}  /* Sentinel */
};

static PyMethodDef TaintRange_methods[] = {
        {"to_string", (PyCFunction) TaintRange_to_string, METH_NOARGS,
                "Return representation of a TaintRange"
        },
        {NULL}  /* Sentinel */
};

static PyTypeObject TaintRangeType = {
        PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = PY_MODULE_NAME_TAINTRANGES,
        .tp_basicsize = sizeof(TaintRange),
        .tp_itemsize = 0,
        .tp_dealloc = (destructor) TaintRange_dealloc,
        .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE,
        .tp_doc = PyDoc_STR("TaintRange objects"),
        .tp_methods = TaintRange_methods,
        .tp_members = TaintRange_members,
        .tp_init = (initproc) TaintRange_init,
        .tp_new = TaintRange_new,
};

#endif //_TAINT_TRACKING_TAINTRANGE_H
