//
// Created by alberto.vara on 16/05/23.
//

#ifndef _TAINT_TRACKING_TAINTRANGE_H
#define _TAINT_TRACKING_TAINTRANGE_H
#include <Python.h>
#include "structmember.h"

#include "../Constants.h"
#include "../Source/Source.h"

#define PY_MODULE_NAME_TAINTRANGES PY_MODULE_NAME "." "TaintRange"


struct TaintRange {
    PyObject_HEAD
    long start{};
    long length{};
    Source source;

    TaintRange() = default;

    TaintRange(long start, long length, Source source)
            : start(start),
              length(length),
              source(std::move(source)){}

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
    Py_XDECREF(self->start);
    Py_XDECREF(self->length);
    // TODO: dealloc source
    //  Py_XDECREF(self->source);
    Py_TYPE(self)->tp_free((PyObject *) self);
}


static PyObject *
TaintRange_new(PyTypeObject *type, PyObject *args, PyObject *kwds)
{
    TaintRange *self;
    self = (TaintRange *) type->tp_alloc(type, 0);
    if (self != NULL) {
//        self->source = Source();
//        if (self->source == NULL) {
//            Py_DECREF(self);
//            return NULL;
//        }

        self->start = 0;
        self->length = 0;
    }
    return (PyObject *) self;
}

static int
TaintRange_init(TaintRange *self, PyObject *args, PyObject *kwds)
{
    static char *kwlist[] = {"start", "length", "source", NULL};
    PyObject *source = NULL, *tmp;

    if (!PyArg_ParseTupleAndKeywords(args, kwds, "|iiO", kwlist,
                                     &self->start, &self->length,
                                     &source))
        return -1;

    if (source) {
        // TODO: assign source
//        tmp = self->source;
//        Py_INCREF(source);
//        self->source = source;
        Py_XDECREF(tmp);
    }

    return 0;
}

static PyObject *
TaintRange_to_string(TaintRange *self, PyObject *Py_UNUSED(ignored))
{
    if (self->start == NULL) {
        PyErr_SetString(PyExc_AttributeError, "start");
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
        {"start", T_INT, offsetof(TaintRange, start), 0,
                "TaintRange start"},
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
