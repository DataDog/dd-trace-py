#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include "structmember.h"

#include <stddef.h>

#include <chrono>
#include <condition_variable>
#include <memory>
#include <mutex>
#include <thread>

// ----------------------------------------------------------------------------
/**
 * Ensure that the GIL is held.
 */
class GILGuard
{
  public:
    inline GILGuard()
    {
        if (!_Py_IsFinalizing())
            _state = PyGILState_Ensure();
    }
    inline ~GILGuard()
    {
        if (PyGILState_Check())
            PyGILState_Release(_state);
    }

  private:
    PyGILState_STATE _state;
};

// ----------------------------------------------------------------------------
/**
 * Release the GIL to allow other threads to run.
 */
class AllowThreads
{
  public:
    inline AllowThreads()
    {
        if (!_Py_IsFinalizing())
            _state = PyEval_SaveThread();
    }
    inline ~AllowThreads()
    {
        if (!_Py_IsFinalizing())
            PyEval_RestoreThread(_state);
    }

  private:
    PyThreadState* _state;
};

// ----------------------------------------------------------------------------
class PyRef
{
  public:
    inline PyRef(PyObject* obj)
      : _obj(obj)
    {
        Py_INCREF(_obj);
    }
    inline ~PyRef() { Py_DECREF(_obj); }

  private:
    PyObject* _obj;
};

// ----------------------------------------------------------------------------
class Event
{
  public:
    void set()
    {
        std::lock_guard<std::mutex> lock(_mutex);
        _set = true;
        _cond.notify_all();
    }

    void wait()
    {
        std::unique_lock<std::mutex> lock(_mutex);
        _cond.wait(lock, [this]() { return _set; });
    }

    bool wait(std::chrono::milliseconds timeout)
    {
        std::unique_lock<std::mutex> lock(_mutex);
        return _cond.wait_for(lock, timeout, [this]() { return _set; });
    }

    void clear()
    {
        std::lock_guard<std::mutex> lock(_mutex);
        _set = false;
    }

  private:
    std::condition_variable _cond;
    std::mutex _mutex;
    bool _set = false;
};

// ----------------------------------------------------------------------------
typedef struct periodic_thread
{
    PyObject_HEAD

      double interval;
    PyObject* name;
    PyObject* ident;

    PyObject* _target;
    PyObject* _on_shutdown;

    PyObject* _ddtrace_profiling_ignore;

    bool _stopping;
    bool _atexit;
    bool _after_fork;

    std::unique_ptr<Event> _started;
    std::unique_ptr<Event> _stopped;
    std::unique_ptr<Event> _request;
    std::unique_ptr<Event> _served;

    std::unique_ptr<std::mutex> _awake_mutex;

    std::unique_ptr<std::thread> _thread;
} PeriodicThread;

// ----------------------------------------------------------------------------
// Maintain a mapping of thread ID to PeriodicThread objects. This is similar
// to threading._active.
static PyObject* _periodic_threads = NULL;

// ----------------------------------------------------------------------------
static PyMemberDef PeriodicThread_members[] = {
    { "interval", T_DOUBLE, offsetof(PeriodicThread, interval), 0, "thread interval" },

    { "name", T_OBJECT_EX, offsetof(PeriodicThread, name), 0, "thread name" },
    { "ident", T_OBJECT_EX, offsetof(PeriodicThread, ident), 0, "thread ID" },

    { "_ddtrace_profiling_ignore",
      T_OBJECT_EX,
      offsetof(PeriodicThread, _ddtrace_profiling_ignore),
      0,
      "whether to ignore the thread for profiling" },

    { NULL } /* Sentinel */
};

// ----------------------------------------------------------------------------
static int
PeriodicThread_init(PeriodicThread* self, PyObject* args, PyObject* kwargs)
{
    static const char* kwlist[] = { "interval", "target", "name", "on_shutdown", NULL };

    self->name = Py_None;
    self->_on_shutdown = Py_None;

    if (!PyArg_ParseTupleAndKeywords(
          args, kwargs, "dO|OO", (char**)kwlist, &self->interval, &self->_target, &self->name, &self->_on_shutdown))
        return -1;

    Py_INCREF(self->_target);
    Py_INCREF(self->name);
    Py_INCREF(self->_on_shutdown);

    Py_INCREF(Py_None);
    self->ident = Py_None;

    Py_INCREF(Py_True);
    self->_ddtrace_profiling_ignore = Py_True;

    self->_stopping = false;
    self->_atexit = false;
    self->_after_fork = false;

    self->_started = std::make_unique<Event>();
    self->_stopped = std::make_unique<Event>();
    self->_request = std::make_unique<Event>();
    self->_served = std::make_unique<Event>();

    self->_awake_mutex = std::make_unique<std::mutex>();

    return 0;
}

// ----------------------------------------------------------------------------
static inline bool
PeriodicThread__periodic(PeriodicThread* self)
{
    PyObject* result = PyObject_CallObject(self->_target, NULL);

    if (result == NULL) {
        PyErr_Print();
    }

    Py_XDECREF(result);

    return result == NULL;
}

// ----------------------------------------------------------------------------
static inline void
PeriodicThread__on_shutdown(PeriodicThread* self)
{
    PyObject* result = PyObject_CallObject(self->_on_shutdown, NULL);

    if (result == NULL) {
        PyErr_Print();
    }

    Py_XDECREF(result);
}

// ----------------------------------------------------------------------------
static PyObject*
PeriodicThread_start(PeriodicThread* self, PyObject* args)
{
    if (self->_thread != nullptr) {
        PyErr_SetString(PyExc_RuntimeError, "Thread already started");
        return NULL;
    }

    if (self->_stopping)
        Py_RETURN_NONE;

    // Start the thread
    self->_thread = std::make_unique<std::thread>([self]() {
        GILGuard _gil;

        PyRef _((PyObject*)self);

        // Retrieve the thread ID
        {
            Py_DECREF(self->ident);
            self->ident = PyLong_FromLong((long)PyThreadState_Get()->thread_id);

            // Map the PeriodicThread object to its thread ID
            PyDict_SetItem(_periodic_threads, self->ident, (PyObject*)self);
        }

        // Mark the thread as started from this point.
        self->_started->set();

        bool error = false;
        auto interval = std::chrono::milliseconds((long long)(self->interval * 1000));

        while (!self->_stopping) {
            {
                AllowThreads _;

                if (self->_request->wait(interval)) {
                    if (self->_stopping)
                        break;

                    // Awake signal
                    self->_request->clear();
                    self->_served->set();
                }
            }

            if (_Py_IsFinalizing())
                break;

            if (PeriodicThread__periodic(self)) {
                // Error
                error = true;
                break;
            }
        }

        // Run the shutdown callback if there was no error and we are not
        // at Python shutdown.
        if (!self->_atexit && !error && self->_on_shutdown != Py_None && !_Py_IsFinalizing())
            PeriodicThread__on_shutdown(self);

        // Notify the join method that the thread has stopped
        self->_stopped->set();
    });

    // Detach the thread. We will make our own joinable mechanism.
    self->_thread->detach();

    // Wait for the thread to start
    {
        AllowThreads _;

        self->_started->wait();
    }

    Py_RETURN_NONE;
}

// ----------------------------------------------------------------------------
static PyObject*
PeriodicThread_awake(PeriodicThread* self, PyObject* args)
{
    if (self->_thread == nullptr) {
        PyErr_SetString(PyExc_RuntimeError, "Thread not started");
        return NULL;
    }

    {
        AllowThreads _;
        std::lock_guard<std::mutex> lock(*self->_awake_mutex);

        self->_served->clear();
        self->_request->set();
        self->_served->wait();
    }

    Py_RETURN_NONE;
}

// ----------------------------------------------------------------------------
static PyObject*
PeriodicThread_stop(PeriodicThread* self, PyObject* args)
{
    if (self->_thread == nullptr) {
        PyErr_SetString(PyExc_RuntimeError, "Thread not started");
        return NULL;
    }

    self->_stopping = true;
    self->_request->set();

    Py_RETURN_NONE;
}

// ----------------------------------------------------------------------------
static PyObject*
PeriodicThread_join(PeriodicThread* self, PyObject* args, PyObject* kwargs)
{
    if (self->_thread == nullptr) {
        PyErr_SetString(PyExc_RuntimeError, "Periodic thread not started");
        return NULL;
    }

    if (self->_thread->get_id() == std::this_thread::get_id()) {
        PyErr_SetString(PyExc_RuntimeError, "Cannot join the current periodic thread");
        return NULL;
    }

    if (self->_after_fork) {
        // The thread is no longer running so it makes no sense to join it.
        Py_RETURN_NONE;
    }

    PyObject* timeout = Py_None;

    if (args != NULL && kwargs != NULL) {
        static const char* argnames[] = { "timeout", NULL };
        if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|O", (char**)argnames, &timeout))
            return NULL;
    }

    if (timeout == Py_None) {
        AllowThreads _;

        self->_stopped->wait();
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

        auto interval = std::chrono::milliseconds((long long)(timeout_value * 1000));

        self->_stopped->wait(interval);
    }

    Py_RETURN_NONE;
}

// ----------------------------------------------------------------------------
static PyObject*
PeriodicThread__atexit(PeriodicThread* self, PyObject* args)
{
    self->_atexit = true;

    if (PeriodicThread_stop(self, NULL) == NULL)
        return NULL;

    if (PeriodicThread_join(self, NULL, NULL) == NULL)
        return NULL;

    Py_RETURN_NONE;
}

// ----------------------------------------------------------------------------
static PyObject*
PeriodicThread__after_fork(PeriodicThread* self, PyObject* args)
{
    self->_after_fork = true;

    Py_RETURN_NONE;
}

// ----------------------------------------------------------------------------
static void
PeriodicThread_dealloc(PeriodicThread* self)
{
    // Since the native thread holds a strong reference to this object, we
    // can only get here if the thread has actually stopped.

    if (_Py_IsFinalizing())
        // Do nothing. We are about to terminate and release resources anyway.
        return;

    // If we are trying to stop from the same thread, then we are still running.
    // This should happen rarely, so we don't worry about the memory leak this
    // will cause.
    if (self->_thread != NULL && self->_thread->get_id() == std::this_thread::get_id())
        return;

    // Unmap the PeriodicThread
    if (self->ident != NULL && PyDict_Contains(_periodic_threads, self->ident))
        PyDict_DelItem(_periodic_threads, self->ident);

    Py_XDECREF(self->name);
    Py_XDECREF(self->_target);
    Py_XDECREF(self->_on_shutdown);

    Py_XDECREF(self->ident);
    Py_XDECREF(self->_ddtrace_profiling_ignore);

    self->_thread = nullptr;

    self->_started = nullptr;
    self->_stopped = nullptr;
    self->_request = nullptr;
    self->_served = nullptr;

    self->_awake_mutex = nullptr;

    Py_TYPE(self)->tp_free((PyObject*)self);
}

// ----------------------------------------------------------------------------
static PyMethodDef PeriodicThread_methods[] = {
    { "start", (PyCFunction)PeriodicThread_start, METH_NOARGS, "Start the thread" },
    { "awake", (PyCFunction)PeriodicThread_awake, METH_NOARGS, "Awake the thread" },
    { "stop", (PyCFunction)PeriodicThread_stop, METH_NOARGS, "Stop the thread" },
    { "join", (PyCFunction)PeriodicThread_join, METH_VARARGS | METH_KEYWORDS, "Join the thread" },
    /* Private */
    { "_atexit", (PyCFunction)PeriodicThread__atexit, METH_NOARGS, "Stop the thread at exit" },
    { "_after_fork", (PyCFunction)PeriodicThread__after_fork, METH_NOARGS, "Mark the thread as after fork" },
    { NULL } /* Sentinel */
};

// ----------------------------------------------------------------------------
static PyTypeObject PeriodicThreadType = {
    .ob_base = PyVarObject_HEAD_INIT(NULL, 0).tp_name = "ddtrace.internal._threads.PeriodicThread",
    .tp_basicsize = sizeof(PeriodicThread),
    .tp_itemsize = 0,
    .tp_dealloc = (destructor)PeriodicThread_dealloc,
    .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE,
    .tp_doc = PyDoc_STR("Native thread calling a Python function periodically"),
    .tp_methods = PeriodicThread_methods,
    .tp_members = PeriodicThread_members,
    .tp_init = (initproc)PeriodicThread_init,
    .tp_new = PyType_GenericNew,
};

// ----------------------------------------------------------------------------
static PyMethodDef _threads_methods[] = {
    { NULL, NULL, 0, NULL } /* Sentinel */
};

// ----------------------------------------------------------------------------
static struct PyModuleDef threadsmodule = {
    PyModuleDef_HEAD_INIT,
    "_threads", /* name of module */
    NULL,       /* module documentation, may be NULL */
    -1,         /* size of per-interpreter state of the module,
                   or -1 if the module keeps state in global variables. */
    _threads_methods,
};

// ----------------------------------------------------------------------------
PyMODINIT_FUNC
PyInit__threads(void)
{
    PyObject* m = NULL;

    if (PyType_Ready(&PeriodicThreadType) < 0)
        return NULL;

    _periodic_threads = PyDict_New();
    if (_periodic_threads == NULL)
        return NULL;

    m = PyModule_Create(&threadsmodule);
    if (m == NULL)
        goto error;

    Py_INCREF(&PeriodicThreadType);
    if (PyModule_AddObject(m, "PeriodicThread", (PyObject*)&PeriodicThreadType) < 0) {
        Py_DECREF(&PeriodicThreadType);
        goto error;
    }

    if (PyModule_AddObject(m, "periodic_threads", _periodic_threads) < 0)
        goto error;

    return m;

error:
    Py_XDECREF(_periodic_threads);
    Py_XDECREF(m);

    return NULL;
}
