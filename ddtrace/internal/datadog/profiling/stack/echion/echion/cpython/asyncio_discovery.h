#pragma once

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <optional>

#include <echion/cpython/asyncio_debug.h>

#if PY_VERSION_HEX >= 0x030e0000
// Returns the list-head offsets from the AsyncioDebug table that the given _asyncio module's state points to, once
// they are validated, or no offsets with no Python error set.
//
// Requires an attached thread state. The module may still be executing, in this thread or another: its state is read
// with read_own_memory, and its debug_offsets slot is set last. Runs no Python code and never detaches, so
// finalization cannot start during the call.
std::optional<AsyncioOffsets>
discover_asyncio_offsets(PyObject* asyncio_module);
#endif
