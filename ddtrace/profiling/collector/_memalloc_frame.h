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
 * profiling helpers (ddtrace/internal/datadog/profiling/profiling_helpers/).
 * This file provides the memalloc_get_lineno helper that combines lasti
 * computation with table-pointer fetching and line number parsing.
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

/* Frame access helpers and line table parsing. */
#include "profiling_helpers/frame_accessors.h"
#include "profiling_helpers/linetable_parser.h"

/* Parse the line number for (code, lasti) by reading the line table directly,
 * without calling PyCode_Addr2Line(). Allocation-safe: only pointer arithmetic
 * and struct field reads; no alloc, decref, or exception state touched.
 *
 * Shared primitive for both _memalloc_frame.h (which has the frame pointer
 * and computes lasti) and _memalloc_tb.cpp (which already has lasti and
 * wants to skip the get_lasti call). */
static inline int
memalloc_resolve_lineno(PyCodeObject* code, int lasti)
{
#ifdef _PY310_AND_LATER
    const unsigned char* table = (const unsigned char*)PyBytes_AS_STRING(code->co_linetable);
    Py_ssize_t len = PyBytes_GET_SIZE(code->co_linetable);
#else
    const unsigned char* table = (const unsigned char*)PyBytes_AS_STRING(code->co_lnotab);
    Py_ssize_t len = PyBytes_GET_SIZE(code->co_lnotab);
#endif
    return DataDog::parse_linetable(table, len, lasti, code->co_firstlineno);
}

static inline int
memalloc_get_lineno(DataDog::py_frame_t* frame, PyCodeObject* code)
{
    return memalloc_resolve_lineno(code, DataDog::get_lasti(frame, code));
}
