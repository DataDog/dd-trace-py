#pragma once

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <cstddef>
#include <cstdint>

// Mirrors of the private contextvars.Context, ContextVar and HAMT layouts.
//
// The fields the context reader uses keep their order from CPython v3.9.0 to v3.16.0a2:
// Include/internal/pycore_context.h for the Context and ContextVar, Include/internal/pycore_hamt.h
// (pycore_structs.h from v3.14.0) and Python/hamt.c for the HAMT and its nodes. Only the object header
// changes size, on free-threaded builds, and PyObject_HEAD follows the headers we build against. The
// exception is the ContextVar cache, which free-threaded builds drop from v3.13.0. The
// type objects are private and not exported from v3.12.0, so the reader takes them from live objects
// at startup (see context_labels.cc), which also checks these mirrors.

extern "C"
{
    // struct _pycontextobject
    typedef struct
    {
        PyObject_HEAD PyObject* ctx_prev;
        PyObject* ctx_vars; // PyHamtObject*
        PyObject* ctx_weakreflist;
        int ctx_entered;
    } EchionContextObject;

    // struct _pycontextvarobject. Only used at startup, to build a HAMT collision node.
    typedef struct
    {
        PyObject_HEAD PyObject* var_name;
        PyObject* var_default;
#if PY_VERSION_HEX < 0x030d0000 || !defined(Py_GIL_DISABLED)
        PyObject* var_cached;
        uint64_t var_cached_tsid;
        uint64_t var_cached_tsver;
#endif
        Py_hash_t var_hash;
    } EchionContextVarObject;

    // PyHamtObject
    typedef struct
    {
        PyObject_HEAD PyObject* h_root;
        PyObject* h_weakreflist;
        Py_ssize_t h_count;
    } EchionHamtObject;

    // PyHamtNode_Bitmap without its b_array. b_array holds Py_SIZE(node) pointers: a (key, value) pair per
    // set bit, where a NULL key means the value is a child node.
    typedef struct
    {
        PyObject_VAR_HEAD uint32_t b_bitmap;
    } EchionHamtBitmapHeader;

    // PyHamtNode_Collision without its c_array. c_array holds Py_SIZE(node) pointers: (key, value) pairs
    // whose keys share c_hash.
    typedef struct
    {
        PyObject_VAR_HEAD int32_t c_hash;
    } EchionHamtCollisionHeader;
}

// The pair arrays of bitmap and collision nodes start right after their headers.
constexpr size_t ECHION_HAMT_BITMAP_ARRAY_OFFSET =
  (sizeof(EchionHamtBitmapHeader) + alignof(PyObject*) - 1) / alignof(PyObject*) * alignof(PyObject*);
constexpr size_t ECHION_HAMT_COLLISION_ARRAY_OFFSET =
  (sizeof(EchionHamtCollisionHeader) + alignof(PyObject*) - 1) / alignof(PyObject*) * alignof(PyObject*);

// PyHamtNode_Array: PyObject_HEAD, then 32 child pointers, then a_count.
constexpr size_t ECHION_HAMT_ARRAY_OFFSET = sizeof(PyObject);
