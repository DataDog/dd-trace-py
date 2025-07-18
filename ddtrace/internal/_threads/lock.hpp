#pragma once

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <atomic>
#include <mutex>
#include <set>

std::unique_ptr<std::mutex> _lock_set_mutex = std::make_unique<std::mutex>();

// ----------------------------------------------------------------------------
// Lock class
// ----------------------------------------------------------------------------

typedef struct lock
{
    PyObject_HEAD

      std::atomic<int>
        _locked = 0;

    std::unique_ptr<std::timed_mutex> _mutex = nullptr;
} Lock;

std::set<Lock*> lock_set; // Global set of locks for reset after fork

// ----------------------------------------------------------------------------
static int
Lock_init(Lock* self, PyObject* args, PyObject* kwargs)
{
    self->_mutex = std::make_unique<std::timed_mutex>();

    // Register the lock for reset after fork
    {
        AllowThreads _;

        std::lock_guard<std::mutex> guard(*_lock_set_mutex);

        lock_set.insert(self);
    }

    return 0;
}

// ----------------------------------------------------------------------------
static void
Lock_dealloc(Lock* self)
{
    // Unregister the lock from the global set
    {
        AllowThreads _;

        std::lock_guard<std::mutex> guard(*_lock_set_mutex);

        lock_set.erase(self);
    }

    self->_mutex = nullptr;

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

    if (timeout == Py_None) {
        AllowThreads _;

        self->_mutex->lock();
    } else {
        double timeout_value = 0.0;
        if (PyFloat_Check(timeout)) {
            timeout_value = PyFloat_AsDouble(timeout);
        } else if (PyLong_Check(timeout)) {
            timeout_value = PyLong_AsDouble(timeout);
        } else {
            PyErr_SetString(PyExc_TypeError, "timeout must be a float or an int");
            return NULL;
        }

        AllowThreads _;

        if (!self->_mutex->try_lock_for(std::chrono::milliseconds((long long)(timeout_value * 1000)))) {
            Py_RETURN_FALSE;
        }
    }

    self->_locked = 1;

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

    self->_mutex->unlock();
    self->_locked = 0; // Reset the lock state

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
static PyObject*
Lock_enter(Lock* self, PyObject* args, PyObject* kwargs)
{
    AllowThreads _;

    self->_mutex->lock();

    self->_locked = 1;

    Py_RETURN_NONE;
}

// ----------------------------------------------------------------------------
static PyObject*
Lock_exit(Lock* self, PyObject* args, PyObject* kwargs)
{
    // This method is called when the lock is used in a "with" statement
    if (Lock_release(self) == NULL) {
        return NULL; // Propagate any error from release
    }

    Py_RETURN_FALSE;
}

static inline void
Lock_reset(Lock* self)
{
    self->_mutex = std::make_unique<std::timed_mutex>();
    self->_locked = 0;
}

// ----------------------------------------------------------------------------
static PyMethodDef Lock_methods[] = {
    { "acquire", (PyCFunction)Lock_acquire, METH_VARARGS | METH_KEYWORDS, "Acquire the lock with an optional timeout" },
    { "release", (PyCFunction)Lock_release, METH_NOARGS, "Release the lock" },
    { "locked", (PyCFunction)Lock_locked, METH_NOARGS, "Return whether the lock is acquired" },
    { "__enter__", (PyCFunction)Lock_enter, METH_NOARGS, "Enter the lock context" },
    { "__exit__", (PyCFunction)Lock_exit, METH_VARARGS | METH_KEYWORDS, "Exit the lock context" },
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

// ----------------------------------------------------------------------------
// RLock class
// ----------------------------------------------------------------------------

typedef struct rlock
{
    PyObject_HEAD

      std::atomic<int>
        _locked = 0;

    std::unique_ptr<std::recursive_timed_mutex> _mutex = nullptr;
} RLock;

std::set<RLock*> rlock_set; // Global set of re-entrant locks for reset after fork

// ----------------------------------------------------------------------------
static int
RLock_init(RLock* self, PyObject* args, PyObject* kwargs)
{
    self->_mutex = std::make_unique<std::recursive_timed_mutex>();

    // Register the re-entrant lock for reset after fork
    {
        AllowThreads _;

        std::lock_guard<std::mutex> guard(*_lock_set_mutex);

        rlock_set.insert(self);
    }

    return 0;
}

// ----------------------------------------------------------------------------
static void
RLock_dealloc(RLock* self)
{
    {
        AllowThreads _;

        std::lock_guard<std::mutex> guard(*_lock_set_mutex);

        rlock_set.erase(self);
    }

    self->_mutex = nullptr;

    Py_TYPE(self)->tp_free((PyObject*)self);
}

// ----------------------------------------------------------------------------
static PyObject*
RLock_acquire(RLock* self, PyObject* args, PyObject* kwargs)
{
    // Get timeout argument
    static const char* kwlist[] = { "timeout", NULL };
    PyObject* timeout = Py_None;
    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|O", (char**)kwlist, &timeout)) {
        return NULL;
    }

    if (timeout == Py_None) {
        AllowThreads _;

        self->_mutex->lock();
    } else {
        double timeout_value = 0.0;
        if (PyFloat_Check(timeout)) {
            timeout_value = PyFloat_AsDouble(timeout);
        } else if (PyLong_Check(timeout)) {
            timeout_value = PyLong_AsDouble(timeout);
        } else {
            PyErr_SetString(PyExc_TypeError, "timeout must be a float or an int");
            return NULL;
        }

        AllowThreads _;

        if (!self->_mutex->try_lock_for(std::chrono::milliseconds((long long)(timeout_value * 1000)))) {
            Py_RETURN_FALSE;
        }
    }

    self->_locked++;

    Py_RETURN_TRUE;
}

// ----------------------------------------------------------------------------
static PyObject*
RLock_release(RLock* self)
{
    if (self->_locked <= 0) {
        PyErr_SetString(PyExc_RuntimeError, "Lock is not acquired");
        return NULL;
    }

    self->_mutex->unlock();
    self->_locked--;

    Py_RETURN_NONE;
}

// ----------------------------------------------------------------------------
static PyObject*
RLock_locked(RLock* self)
{
    if (self->_locked > 0) {
        Py_RETURN_TRUE;
    }

    Py_RETURN_FALSE;
}

// ----------------------------------------------------------------------------
static PyObject*
RLock_enter(RLock* self, PyObject* args, PyObject* kwargs)
{
    AllowThreads _;

    self->_mutex->lock();

    self->_locked++;

    Py_RETURN_NONE;
}

// ----------------------------------------------------------------------------
static PyObject*
RLock_exit(RLock* self, PyObject* args, PyObject* kwargs)
{
    // This method is called when the lock is used in a "with" statement
    if (RLock_release(self) == NULL) {
        return NULL; // Propagate any error from release
    }

    Py_RETURN_FALSE;
}

static inline void
RLock_reset(RLock* self)
{
    self->_mutex = std::make_unique<std::recursive_timed_mutex>();
    self->_locked = 0;
}

// ----------------------------------------------------------------------------
static PyMethodDef RLock_methods[] = {
    { "acquire",
      (PyCFunction)RLock_acquire,
      METH_VARARGS | METH_KEYWORDS,
      "Acquire the lock with an optional timeout" },
    { "release", (PyCFunction)RLock_release, METH_NOARGS, "Release the lock" },
    { "locked", (PyCFunction)RLock_locked, METH_NOARGS, "Return whether the lock is acquired at least once" },
    { "__enter__", (PyCFunction)RLock_enter, METH_NOARGS, "Enter the lock context" },
    { "__exit__", (PyCFunction)RLock_exit, METH_VARARGS | METH_KEYWORDS, "Exit the lock context" },
    { NULL } /* Sentinel */
};

// ----------------------------------------------------------------------------
static PyMemberDef RLock_members[] = {
    { NULL } /* Sentinel */
};

// ----------------------------------------------------------------------------
static PyTypeObject RLockType = {
    .ob_base = PyVarObject_HEAD_INIT(NULL, 0).tp_name = "ddtrace.internal._threads.RLock",
    .tp_basicsize = sizeof(RLock),
    .tp_itemsize = 0,
    .tp_dealloc = (destructor)RLock_dealloc,
    .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE,
    .tp_doc = PyDoc_STR("Native re-entrant lock implementation"),
    .tp_methods = RLock_methods,
    .tp_members = RLock_members,
    .tp_init = (initproc)RLock_init,
    .tp_new = PyType_GenericNew,
};

// ----------------------------------------------------------------------------
static PyObject*
lock_reset_locks(PyObject* Py_UNUSED(self), PyObject* Py_UNUSED(args))
{
    // Reset all locks that have been registered for reset after a fork. This
    // MUST be called in a single-thread scenario only, e.g. soon after the
    // fork.
    for (Lock* lock : lock_set) {
        Lock_reset(lock);
    }

    for (RLock* rlock : rlock_set) {
        RLock_reset(rlock);
    }

    // Reset the lock set mutex too!
    _lock_set_mutex = std::make_unique<std::mutex>();

    Py_RETURN_NONE;
}
