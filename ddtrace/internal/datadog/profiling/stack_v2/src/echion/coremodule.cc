// This file is part of "echion" which is released under MIT.
//
// Copyright (c) 2023 Gabriele N. Tornetta <phoenix1987@gmail.com>.

#define PY_SSIZE_T_CLEAN
#include <Python.h>
#if PY_VERSION_HEX >= 0x030c0000
// https://github.com/python/cpython/issues/108216#issuecomment-1696565797
#undef _PyGC_FINALIZED
#endif

#include <mutex>

#include <fcntl.h>
#include <sched.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <time.h>
#include <unistd.h>
#if defined PL_DARWIN
#include <pthread.h>
#endif

#include <echion/config.h>
#include <echion/greenlets.h>
#include <echion/interp.h>
#include <echion/mojo.h>
#include <echion/stacks.h>
#include <echion/state.h>
#include <echion/threads.h>
#include <echion/timing.h>

// ----------------------------------------------------------------------------
static void
_init()
{
    pid = getpid();
}

// ----------------------------------------------------------------------------
static PyObject*
track_thread(PyObject* Py_UNUSED(m), PyObject* args)
{
    uintptr_t thread_id; // map key
    const char* thread_name;
    pid_t native_id;

    if (!PyArg_ParseTuple(args, "lsi", &thread_id, &thread_name, &native_id))
        return NULL;

    {
        const std::lock_guard<std::mutex> guard(thread_info_map_lock);

        auto maybe_thread_info = ThreadInfo::create(thread_id, native_id, thread_name);
        if (!maybe_thread_info) {
            PyErr_SetString(PyExc_RuntimeError, "Failed to track thread");
            return nullptr;
        }

        auto entry = thread_info_map.find(thread_id);
        if (entry != thread_info_map.end()) {
            // Thread is already tracked so we update its info
            entry->second = std::move(*maybe_thread_info);
        } else {
            thread_info_map.emplace(thread_id, std::move(*maybe_thread_info));
        }
    }

    Py_RETURN_NONE;
}

// ----------------------------------------------------------------------------
static PyObject*
untrack_thread(PyObject* Py_UNUSED(m), PyObject* args)
{
    unsigned long thread_id;
    if (!PyArg_ParseTuple(args, "l", &thread_id))
        return NULL;

    {
        const std::lock_guard<std::mutex> guard(thread_info_map_lock);

        thread_info_map.erase(thread_id);
    }

    Py_RETURN_NONE;
}

// ----------------------------------------------------------------------------
static PyObject*
init(PyObject* Py_UNUSED(m), PyObject* Py_UNUSED(args))
{
    _init();

    Py_RETURN_NONE;
}

// ----------------------------------------------------------------------------
static PyMethodDef echion_core_methods[] = {
    { "track_thread", track_thread, METH_VARARGS, "Map the name of a thread with its identifier" },
    { "untrack_thread", untrack_thread, METH_VARARGS, "Untrack a terminated thread" },
    { "init", init, METH_NOARGS, "Initialize the stack sampler (usually after a fork)" },
    // Configuration interface
    { "set_interval", set_interval, METH_VARARGS, "Set the sampling interval" },
    { "set_cpu", set_cpu, METH_VARARGS, "Set whether to use CPU time instead of wall time" },
    { "set_max_frames", set_max_frames, METH_VARARGS, "Set the max number of frames to unwind" },
    // Sentinel
    { NULL, NULL, 0, NULL }
};

// ----------------------------------------------------------------------------
static struct PyModuleDef coremodule = {
    PyModuleDef_HEAD_INIT,
    "core", /* name of module */
    NULL,   /* module documentation, may be NULL */
    -1,     /* size of per-interpreter state of the module,
               or -1 if the module keeps state in global variables. */
    echion_core_methods,
    nullptr, /* m_traverse */
    nullptr, /* m_clear */
    nullptr, /* m_free */
    nullptr, /* m_is_preinitialised */
};

// ----------------------------------------------------------------------------
PyMODINIT_FUNC
PyInit_core(void)
{
    PyObject* m;

    m = PyModule_Create(&coremodule);
    if (m == NULL)
        return NULL;

    // We make the assumption that this module is loaded by the main thread.
    // TODO: These need to be reset after a fork.
    _init();

    return m;
}
