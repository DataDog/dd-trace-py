#pragma once

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <optional>

#include <echion/cpython/asyncio_debug.h>

#if PY_VERSION_HEX >= 0x030e0000
// Finds CPython's AsyncioDebug table through the state of the given _asyncio module and returns its list-head offsets
// once they are validated against Echion's TaskObj mirror and the calling thread's live list head. Returns no offsets
// for anything else, with no Python error set.
//
// Requires an attached thread state. The module may still be executing, in this thread or another: its state is read
// with the system's memory-read syscall, never dereferenced, and its debug_offsets slot, written last, is validated
// before use. The reference is borrowed. Runs no Python code, takes no Python locks and never detaches, so it is safe
// to call while the interpreter may start finalizing.
std::optional<AsyncioOffsets>
discover_asyncio_offsets(PyObject* asyncio_module);
#endif
