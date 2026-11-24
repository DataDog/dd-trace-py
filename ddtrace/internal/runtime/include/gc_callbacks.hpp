#pragma once

// Public C API helpers for a native gc.callbacks entry, shared by separately built
// extensions. Keep this header free of Py_BUILD_CORE and valid on every supported Python
// version: one of its users installs a callback on all of them.

#ifndef PY_SSIZE_T_CLEAN
#define PY_SSIZE_T_CLEAN
#endif
#include <Python.h>

namespace Datadog::gc_callbacks {

enum class Phase
{
    start,
    stop,
    other,
};

// Never leaves an exception set.
inline Phase
parse_phase(PyObject* phase) noexcept
{
    if (phase == nullptr || !PyUnicode_Check(phase)) {
        return Phase::other;
    }

    const bool is_start = PyUnicode_CompareWithASCIIString(phase, "start") == 0;
    const bool is_stop = !is_start && PyUnicode_CompareWithASCIIString(phase, "stop") == 0;
    if (PyErr_Occurred()) {
        PyErr_Clear();
        return Phase::other;
    }

    if (is_start) {
        return Phase::start;
    }
    return is_stop ? Phase::stop : Phase::other;
}

// The key for parse_generation(): a new reference, or NULL with an exception set.
// PyDict_GetItemString would build a new key string on every call, and a GC callback must
// not allocate.
inline PyObject*
new_generation_key() noexcept
{
    return PyUnicode_InternFromString("generation");
}

// info["generation"] when it is a non-negative int that fits a long, else -1. Never
// allocates and never leaves an exception set.
inline long
parse_generation(PyObject* info, PyObject* generation_key) noexcept
{
    if (info == nullptr || generation_key == nullptr || !PyDict_Check(info)) {
        return -1;
    }

#if PY_VERSION_HEX >= 0x030d0000
    PyObject* value = nullptr;
    if (PyDict_GetItemRef(info, generation_key, &value) <= 0) {
        PyErr_Clear();
        return -1;
    }
    const long generation = PyLong_Check(value) ? PyLong_AsLong(value) : -1;
    Py_DECREF(value);
#else
    PyObject* value = PyDict_GetItemWithError(info, generation_key);
    if (value == nullptr) {
        PyErr_Clear();
        return -1;
    }
    const long generation = PyLong_Check(value) ? PyLong_AsLong(value) : -1;
#endif

    if (generation < 0) {
        PyErr_Clear();
        return -1;
    }
    return generation;
}

// A new reference to the gc.callbacks list, or NULL with an exception set.
inline PyObject*
get_list() noexcept
{
    PyObject* gc_module = PyImport_ImportModule("gc");
    if (gc_module == nullptr) {
        return nullptr;
    }

    PyObject* callbacks = PyObject_GetAttrString(gc_module, "callbacks");
    Py_DECREF(gc_module);
    if (callbacks == nullptr) {
        return nullptr;
    }

    if (!PyList_Check(callbacks)) {
        Py_DECREF(callbacks);
        PyErr_SetString(PyExc_RuntimeError, "gc.callbacks is not a list");
        return nullptr;
    }

    return callbacks;
}

// Appends callback unless that exact object is already in callbacks. Returns 1 when it
// appended, 0 when callback was already there, and -1 with an exception set.
//
// Free-threaded builds scan and append inside the list's critical section. On 3.13 that is
// not atomic: PyList_Append enters the same critical section, and before 3.14 the nested
// entry suspends the outer one. Callers that must never register twice also serialize
// their installs.
inline int
append_once(PyObject* callbacks, PyObject* callback) noexcept
{
    int result = 1;
#if PY_VERSION_HEX >= 0x030d0000
    Py_BEGIN_CRITICAL_SECTION(callbacks);
#endif

    for (Py_ssize_t i = 0; i < PyList_GET_SIZE(callbacks); ++i) {
        if (PyList_GET_ITEM(callbacks, i) == callback) {
            result = 0;
            break;
        }
    }
    if (result == 1 && PyList_Append(callbacks, callback) < 0) {
        result = -1;
    }

#if PY_VERSION_HEX >= 0x030d0000
    Py_END_CRITICAL_SECTION();
#endif
    return result;
}

// Removes every entry that is callback itself. Identity comparisons only, so no entry's
// __eq__ runs. The caller must hold its own reference to callback, so that no removal
// deallocates it. Returns the number of entries removed, or -1 with an exception set.
inline Py_ssize_t
remove_all(PyObject* callbacks, PyObject* callback) noexcept
{
    Py_ssize_t removed = 0;
#if PY_VERSION_HEX >= 0x030d0000
    Py_BEGIN_CRITICAL_SECTION(callbacks);
#endif

    for (Py_ssize_t i = PyList_GET_SIZE(callbacks); i-- > 0;) {
        if (PyList_GET_ITEM(callbacks, i) != callback) {
            continue;
        }
        if (PySequence_DelItem(callbacks, i) < 0) {
            removed = -1;
            break;
        }
        ++removed;
    }

#if PY_VERSION_HEX >= 0x030d0000
    Py_END_CRITICAL_SECTION();
#endif
    return removed;
}

} // namespace Datadog::gc_callbacks
