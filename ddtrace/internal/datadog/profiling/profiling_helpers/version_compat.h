#pragma once

/* Shared Python version compatibility for profiling code.
 *
 * This header centralizes:
 *   - CPython internal header includes (version-conditional)
 *   - Frame type alias (DataDog::py_frame_t)
 *   - _PyStackRef alignment assertions (Python 3.14+)
 *
 * Prerequisites: the translation unit must define Py_BUILD_CORE and include
 * <Python.h> before including this header.  Both the memalloc allocator-hook
 * profiler and the echion sampling-thread profiler satisfy this requirement
 * in their own top-level headers.
 */

#ifndef Py_PYTHON_H
#error "version_compat.h requires Python.h to be included first (with Py_BUILD_CORE defined)"
#endif

#if !defined(Py_BUILD_CORE) && !defined(Py_BUILD_CORE_BUILTIN) && !defined(Py_BUILD_CORE_MODULE)
#error "version_compat.h requires Py_BUILD_CORE to be defined before Python.h"
#endif

#include <frameobject.h>

/* Include CPython internal frame headers for zero-refcount frame walking.
 * Python 3.14+: _PyInterpreterFrame moved to pycore_interpframe_structs.h
 * Python 3.11-3.13: _PyInterpreterFrame is in pycore_frame.h */
#if PY_VERSION_HEX >= 0x030b0000
#if PY_VERSION_HEX >= 0x030e0000
#include <internal/pycore_interpframe_structs.h>
#else
#include <internal/pycore_frame.h>
#endif /* PY_VERSION_HEX >= 0x030e0000 */
#include <internal/pycore_code.h>
#endif /* PY_VERSION_HEX >= 0x030b0000 */

/* On Python 3.14+, f_executable is a _PyStackRef tagged pointer.
 * Assert the alignment invariant that tag masking relies on.
 * Expected on our supported 64-bit builds. */
#if PY_VERSION_HEX >= 0x030e0000
static_assert(alignof(PyObject) >= 8, "PyObject must be at least 8-byte aligned for _PyStackRef tag masking");
#endif /* PY_VERSION_HEX >= 0x030e0000 */

namespace DataDog {

#if PY_VERSION_HEX >= 0x030b0000
using py_frame_t = _PyInterpreterFrame;
#else
using py_frame_t = PyFrameObject;
#endif /* PY_VERSION_HEX >= 0x030b0000 */

} /* namespace DataDog */
