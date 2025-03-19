#pragma once

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <atomic>
#include <mutex>

// ----------------------------------------------------------------------------
typedef struct lock
{
    PyObject_HEAD

      std::atomic<int>
        _locked = 0;

    std::unique_ptr<std::timed_mutex> _mutex = nullptr;
    std::unique_ptr<std::recursive_timed_mutex> _rmutex = nullptr;
} Lock;

// ----------------------------------------------------------------------------
static int
Lock_init(Lock* self, PyObject* args, PyObject* kwargs)
{
    // Get the reentrant argument
    static const char* kwlist[] = { "reentrant", NULL };
    PyObject* reentrant = NULL;
    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|O", (char**)kwlist, &reentrant)) {
        return -1;
    }

    // if reentrant was requested use a recursive mutex
    if (PyObject_IsTrue(reentrant)) {
        self->_rmutex = std::make_unique<std::recursive_timed_mutex>();
    } else {
        self->_mutex = std::make_unique<std::timed_mutex>();
    }

    return 0;
}

// ----------------------------------------------------------------------------
static void
Lock_dealloc(Lock* self)
{
    self->_mutex = nullptr;
    self->_rmutex = nullptr;

    Py_TYPE(self)->tp_free((PyObject*)self);
}

// ----------------------------------------------------------------------------
static PyObject*
Lock_acquire(Lock* self, PyObject* args, PyObject* kwargs)
{
    // Get timeout argument
    static const char* kwlist[] = { "timeout", NULL };
    PyObject* timeout = Py_None;
    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|O", (char**)kwlist, &timeout)) {
        return NULL;
    }

    double timeout_value = 0.0;
    if (timeout != Py_None) {
        if (PyFloat_Check(timeout)) {
            timeout_value = PyFloat_AsDouble(timeout);
        } else if (PyLong_Check(timeout)) {
            timeout_value = PyLong_AsDouble(timeout);
        } else {
            PyErr_SetString(PyExc_TypeError, "timeout must be a float or an int");
            return NULL;
        }
    }

    if (self->_mutex != nullptr) {
        AllowThreads _;

        if (timeout == Py_None) {

            self->_mutex->lock();
        } else if (!self->_mutex->try_lock_for(std::chrono::milliseconds((long long)(timeout_value * 1000)))) {
            Py_RETURN_FALSE;
        }
        self->_locked = 1;
    } else if (self->_rmutex != nullptr) {
        AllowThreads _;

        if (timeout == Py_None) {
            self->_rmutex->lock();
        } else if (!self->_rmutex->try_lock_for(std::chrono::milliseconds((long long)(timeout_value * 1000)))) {
            Py_RETURN_FALSE;
        }
        self->_locked++;
    } else {
        PyErr_SetString(PyExc_RuntimeError, "Lock not initialized");
        return NULL;
    }

    Py_RETURN_TRUE;
}

// ----------------------------------------------------------------------------
static PyObject*
Lock_release(Lock* self)
{
    if (self->_locked <= 0) {
        PyErr_SetString(PyExc_RuntimeError, "Lock is not acquired");
        return NULL;
    }

    if (self->_mutex != nullptr) {
        self->_mutex->unlock();
        self->_locked = 0; // Reset the lock state
    } else if (self->_rmutex != nullptr) {
        self->_rmutex->unlock();
        self->_locked--; // Decrement the lock count for reentrant locks
    } else {
        PyErr_SetString(PyExc_RuntimeError, "Lock not initialized");
        return NULL;
    }

    Py_RETURN_NONE;
}

// ----------------------------------------------------------------------------
static PyObject*
Lock_locked(Lock* self)
{
    if (self->_locked > 0) {
        Py_RETURN_TRUE;
    }

    Py_RETURN_FALSE;
}

// ----------------------------------------------------------------------------
static PyMethodDef Lock_methods[] = {
    { "acquire", (PyCFunction)Lock_acquire, METH_VARARGS | METH_KEYWORDS, "Acquire the lock with an optional timeout" },
    { "release", (PyCFunction)Lock_release, METH_NOARGS, "Release the lock" },
    { "locked", (PyCFunction)Lock_locked, METH_NOARGS, "Return whether the lock is acquired" },
    { NULL } /* Sentinel */
};

// ----------------------------------------------------------------------------
static PyMemberDef Lock_members[] = {
    { NULL } /* Sentinel */
};

// ----------------------------------------------------------------------------
static PyTypeObject LockType = {
    .ob_base = PyVarObject_HEAD_INIT(NULL, 0).tp_name = "ddtrace.internal._threads.Lock",
    .tp_basicsize = sizeof(Lock),
    .tp_itemsize = 0,
    .tp_dealloc = (destructor)Lock_dealloc,
    .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE,
    .tp_doc = PyDoc_STR("Native lock implementation"),
    .tp_methods = Lock_methods,
    .tp_members = Lock_members,
    .tp_init = (initproc)Lock_init,
    .tp_new = PyType_GenericNew,
};
