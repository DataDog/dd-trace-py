#ifndef _TAINT_TRACKING_TAINTRANGE_H
#define _TAINT_TRACKING_TAINTRANGE_H
#include <Python.h>
#include "structmember.h"
#include "absl/container/node_hash_map.h"

#include "../Constants.h"
#include "../Source/Source.h"
#include "../TaintedObject/TaintedObject.h"

#define PY_MODULE_NAME_TAINTRANGES PY_MODULE_NAME "." "TaintRange"

// Alias
using TaintedObjectPtr = TaintedObject*;
using TaintRangeMapType = absl::node_hash_map<uintptr_t, TaintedObjectPtr>;

struct TaintRange {
    PyObject_HEAD
    int start;
    int length;
    Source* source = nullptr;

    TaintRange() = default;

    TaintRange(long start, long length, Source* source)
            : start(start),
              length(length),
              source(source){}

    void reset();

    [[nodiscard]] string toString() const;

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
    if (self->source) {
        Py_XDECREF(self->source);
    }
    Py_TYPE(self)->tp_free((PyObject *) self);
}


static PyObject *
TaintRange_new(PyTypeObject *type, PyObject *args, PyObject *kwds)
{
    TaintRange *self;
    self = (TaintRange *) type->tp_alloc(type, 0);
    if (self != NULL) {
        self->source = new Source();
        if (self->source == nullptr) {
            Py_DECREF(self);
            return nullptr;
        }

        self->start = 0;
        self->length = 0;
    }
    return (PyObject *) self;
}

static int
TaintRange_init(TaintRange *self, PyObject *args, PyObject *kwds)
{
    static char *kwlist[] = {"start", "length", "source", NULL};
    PyObject *pysource = nullptr;

    if (!PyArg_ParseTupleAndKeywords(args, kwds, "|iiO", kwlist,
                                     &self->start, &self->length, &pysource))
        return -1;

    if (pysource) {
        self->source = (Source*)pysource;
        Py_INCREF(self->source);
    }

    return 0;
}

static PyObject *
TaintRange_to_string(TaintRange *self, PyObject *Py_UNUSED(ignored))
{
    if (self->start == -1) {
        PyErr_SetString(PyExc_AttributeError, "start");
        return nullptr;
    }
    if (self->length == -1) {
        PyErr_SetString(PyExc_AttributeError, "length");
        return nullptr;
    }
    return PyUnicode_FromFormat("%s", self->toString().c_str());
}


static PyMemberDef TaintRange_members[] = {
        {"length", T_INT, offsetof(TaintRange, length), 0,
                "TaintRange last name"},
        {"start", T_INT, offsetof(TaintRange, start), 0,
                "TaintRange start"},
        {"source", T_OBJECT, offsetof(TaintRange, source), 0,
                "TaintRange source"},
        {nullptr}  /* Sentinel */
};

static PyMethodDef TaintRange_methods[] = {
        {"to_string", (PyCFunction) TaintRange_to_string, METH_NOARGS,
                "Return representation of a TaintRange"
        },
        {nullptr}  /* Sentinel */
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
