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
 * not to allocate.  The actual parsing is delegated to DataDog::parse_linetable()
 * which performs pure byte arithmetic.
 *
 * Allocation safety: this function only performs pointer arithmetic and byte
 * reads from already-owned objects. It does not allocate, decref, or touch
 * Python exception state. */
static inline int
memalloc_get_lineno(DataDog::py_frame_t* frame, PyCodeObject* code)
{
    int lasti = DataDog::get_lasti(frame, code);

#ifdef _PY310_AND_LATER
    const unsigned char* table = (const unsigned char*)PyBytes_AS_STRING(code->co_linetable);
    Py_ssize_t len = PyBytes_GET_SIZE(code->co_linetable);
#else
    const unsigned char* table = (const unsigned char*)PyBytes_AS_STRING(code->co_lnotab);
    Py_ssize_t len = PyBytes_GET_SIZE(code->co_lnotab);
#endif

    return DataDog::parse_linetable(table, len, lasti, code->co_firstlineno);
}
