#pragma once

/* Version-specific frame-walking helpers for the memalloc profiler.
 *
 * All helpers use direct struct field reads: no new Python references are
 * created, no Py_INCREF/Py_DECREF, and no calls that can allocate or free
 * Python objects.
 * This is critical because these helpers run inside CPython's
 * PYMEM_DOMAIN_OBJ allocator hook.
 *
 * This header must be the first Python header included by a translation unit.
 * It defines Py_BUILD_CORE before including Python.h so the CPython internal
 * headers below are declared consistently.
 *
 * The core frame-access logic and line table parsing are provided by the
 * shared profiling headers (ddtrace/internal/datadog/profiling/shared/).
 * This file provides memalloc-specific aliases and the lasti-to-lineno
 * helper that combines frame access with table parsing.
 */

#ifdef Py_PYTHON_H
#error "_memalloc_frame.h must be included before Python.h so Py_BUILD_CORE applies to CPython internals"
#endif // Py_PYTHON_H

#define Py_BUILD_CORE
#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include "_pymacro.h"

#ifdef Py_GIL_DISABLED
#error "_memalloc frame walking relies on the GIL-held allocator hook and is not yet supported on free-threaded CPython"
#endif // Py_GIL_DISABLED

// AIDEV-TODO: Revisit direct frame walking and heap-tracker synchronization if memalloc adds Py_GIL_DISABLED support.

/* Shared profiling headers — frame accessors, line table parsing, and
 * version compatibility are the single source of truth shared with echion. */
#include "shared/frame_accessors.h"
#include "shared/linetable_parser.h"

/* Memalloc frame type — alias of the shared dd_py_frame_t. */
using memalloc_frame_t = dd_py_frame_t;

/* ---- Thin wrappers preserving the memalloc_ API ----------------------- */

static inline memalloc_frame_t*
memalloc_get_frame_from_thread_state(PyThreadState* tstate)
{
    return dd_py_frame_get_frame_from_tstate(tstate);
}

static inline memalloc_frame_t*
memalloc_get_previous_frame(memalloc_frame_t* frame)
{
    return dd_py_frame_get_previous_frame(frame);
}

static inline PyCodeObject*
memalloc_get_code_from_frame(memalloc_frame_t* frame)
{
    return dd_py_frame_get_code_from_frame(frame);
}

static inline bool
memalloc_should_skip_frame(memalloc_frame_t* frame)
{
    return dd_py_frame_should_skip_frame(frame);
}

static inline PyObject*
memalloc_get_code_name(PyCodeObject* code)
{
    return dd_py_frame_get_code_name(code);
}

/* Return the current line number for the frame by parsing the line table
 * directly, without calling PyCode_Addr2Line().
 *
 * We avoid PyCode_Addr2Line because CPython does not guarantee it is
 * allocation-free, and we are called from inside the allocator hook where
 * any allocation or free would cause reentrant undefined behaviour.
 *
 * The only CPython APIs used to obtain the table bytes are
 * PyBytes_AS_STRING / PyBytes_GET_SIZE, which are macros expanding to
 * struct field reads on PyBytesObject (ob_sval / ob_size) — guaranteed
 * not to allocate.  The actual parsing is delegated to the shared
 * dd_py_frame_parse_linetable() which performs pure byte arithmetic.
 *
 * Allocation safety: this function only performs pointer arithmetic and byte
 * reads from already-owned objects. It does not allocate, decref, or touch
 * Python exception state. */
static inline int
memalloc_get_lineno(memalloc_frame_t* frame, PyCodeObject* code)
{
    int lasti = dd_py_frame_get_lasti(frame, code);

#ifdef _PY310_AND_LATER
    const unsigned char* table = (const unsigned char*)PyBytes_AS_STRING(code->co_linetable);
    Py_ssize_t len = PyBytes_GET_SIZE(code->co_linetable);
#else
    const unsigned char* table = (const unsigned char*)PyBytes_AS_STRING(code->co_lnotab);
    Py_ssize_t len = PyBytes_GET_SIZE(code->co_lnotab);
#endif

    return dd_py_frame_parse_linetable(table, len, lasti, code->co_firstlineno);
}
