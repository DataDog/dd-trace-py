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
 */

#ifdef Py_PYTHON_H
#error "_memalloc_frame.h must be included before Python.h so Py_BUILD_CORE applies to CPython internals"
#endif // Py_PYTHON_H

#define Py_BUILD_CORE
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <frameobject.h>

#include "_pymacro.h"

#ifdef Py_GIL_DISABLED
#error "_memalloc frame walking relies on the GIL-held allocator hook and is not yet supported on free-threaded CPython"
#endif // Py_GIL_DISABLED

// AIDEV-TODO: Revisit direct frame walking and heap-tracker synchronization if memalloc adds Py_GIL_DISABLED support.

/* Include CPython internal frame headers for zero-refcount frame walking.
 * Python 3.11+: _PyInterpreterFrame is needed for direct frame chain walking.
 * Python 3.14+: definition moved to pycore_interpframe_structs.h
 * Python 3.11-3.13: definition is in pycore_frame.h */
#ifdef _PY311_AND_LATER
#ifdef _PY314_AND_LATER
#include <internal/pycore_interpframe_structs.h>
#else
#include <internal/pycore_frame.h>
#endif // _PY314_AND_LATER
#include <internal/pycore_code.h>
using memalloc_frame_t = _PyInterpreterFrame;
#else
using memalloc_frame_t = PyFrameObject;
#endif // _PY311_AND_LATER

#ifdef _PY314_AND_LATER
// Expected on our supported 64-bit builds; assert the exact alignment
// invariant that the _PyStackRef tag masking relies on.
static_assert(alignof(PyObject) >= 8,
              "PyObject must remain at least 8-byte aligned for _PyStackRef tag masking to be safe");
#endif // _PY314_AND_LATER

/* Return the innermost interpreter frame from the thread state without
 * incrementing any reference count. Returns a borrowed frame pointer. */
static inline memalloc_frame_t*
memalloc_get_frame_from_thread_state(PyThreadState* tstate)
{
#ifdef _PY313_AND_LATER
    /* Python 3.13+: current_frame is directly on PyThreadState. */
    return tstate->current_frame;
#elif defined(_PY311_AND_LATER)
    /* Python 3.11-3.12: current_frame is on the _PyCFrame.
     * cframe can be NULL while a thread is still being initialized or torn down. */
    return tstate->cframe ? tstate->cframe->current_frame : NULL;
#else
    /* Pre-3.11: tstate->frame is a public PyFrameObject*. */
    return tstate->frame;
#endif // _PY313_AND_LATER
}

/* Return the caller's frame (one level up the call stack) without creating
 * a new reference. */
static inline memalloc_frame_t*
memalloc_get_previous_frame(memalloc_frame_t* frame)
{
#ifdef _PY311_AND_LATER
    return frame->previous;
#else
    return frame->f_back;
#endif // _PY311_AND_LATER
}

/* Return the code object for the frame as a borrowed reference (no INCREF).
 * For Python 3.14+, f_executable carries tagged pointer bits that must be
 * masked off before treating it as a PyObject*. */
static inline PyCodeObject*
memalloc_get_code_from_frame(memalloc_frame_t* frame)
{
#ifdef _PY314_AND_LATER
    /* Python 3.14+: f_executable is a _PyStackRef (tagged pointer).
     * Clear the tag bits to recover the PyObject* pointer.
     * Masking with ~7 (clearing 3 lowest bits) safely covers all configs
     * (debug, free-threading, release), since PyObject* is always aligned
     * to at least 8 bytes. */
    return (PyCodeObject*)((uintptr_t)frame->f_executable.bits & ~(uintptr_t)7);
#elif defined(_PY313_AND_LATER)
    /* Python 3.13: f_executable is an untagged PyObject*. */
    return (PyCodeObject*)frame->f_executable;
#elif defined(_PY311_AND_LATER)
    /* Python 3.11-3.12: f_code is a direct PyCodeObject*. */
    return frame->f_code;
#else
    /* Pre-3.11: f_code is a public PyCodeObject*. */
    return frame->f_code;
#endif // _PY314_AND_LATER
}

/* Return true for frames that should be skipped during stack walking:
 *   - frames whose code slot is NULL or not a real PyCodeObject
 *   - Python 3.12+ "cstack" shim frames used during generator/coroutine entry
 *   - Python 3.14+ interpreter-owned shim frames
 *   - Python 3.11 incomplete frames (prev_instr < firsttraceable) */
static inline bool
memalloc_should_skip_frame(memalloc_frame_t* frame)
{
    PyObject* code = (PyObject*)memalloc_get_code_from_frame(frame);
    if (code == NULL || !PyCode_Check(code)) {
        return true;
    }

#ifdef _PY312_AND_LATER
    return frame->owner != FRAME_OWNED_BY_THREAD && frame->owner != FRAME_OWNED_BY_GENERATOR;
#elif defined(_PY311_AND_LATER)
    return _PyFrame_IsIncomplete(frame);
#else
    return false;
#endif // _PY312_AND_LATER
}

/* Varint helpers for parsing the 3.11+ location table (PEP 657).
 * These read from the co_linetable byte array — pure byte reads,
 * no allocations or frees. */
#ifdef _PY311_AND_LATER
static inline int
memalloc_read_varint(const unsigned char* table, Py_ssize_t len, Py_ssize_t* i)
{
    Py_ssize_t guard = len - 1;
    if (*i >= guard)
        return 0;
    int val = table[++*i] & 63;
    int shift = 0;
    while (*i < guard && table[*i] & 64) {
        shift += 6;
        val |= (table[++*i] & 63) << shift;
    }
    return val;
}

static inline int
memalloc_read_signed_varint(const unsigned char* table, Py_ssize_t len, Py_ssize_t* i)
{
    int val = memalloc_read_varint(table, len, i);
    return (val & 1) ? -(val >> 1) : (val >> 1);
}
#endif // _PY311_AND_LATER

/* Return the current line number for the frame by parsing the line table
 * directly, without calling PyCode_Addr2Line().
 *
 * We avoid PyCode_Addr2Line because CPython does not guarantee it is
 * allocation-free, and we are called from inside the allocator hook where
 * any allocation or free would cause reentrant undefined behaviour.
 *
 * Instead we parse co_linetable (3.10+) or co_lnotab (3.9) inline.
 * The only CPython APIs used are PyBytes_AS_STRING / PyBytes_GET_SIZE,
 * which are macros expanding to struct field reads on PyBytesObject
 * (ob_sval / ob_size) — guaranteed not to allocate.
 *
 * The parsing logic is ported from the stack profiler's
 * Frame::infer_location() (ddtrace/internal/datadog/profiling/stack/
 * src/echion/frame.cc) which handles all supported CPython versions.
 *
 * AIDEV-TODO: Unify this version-specific line table parsing with the stack
 * profiler's Frame::infer_location() implementation so both profilers share a
 * single source of truth for CPython location decoding.
 *
 * Allocation safety: this function only performs pointer arithmetic and byte
 * reads from already-owned objects. It does not allocate, decref, or touch
 * Python exception state. */
static inline int
memalloc_get_lineno(memalloc_frame_t* frame, PyCodeObject* code)
{
    int lasti;

#ifdef _PY313_AND_LATER
    /* Python 3.13+: instr_ptr points to the NEXT instruction.
     * Result is in _Py_CODEUNIT units. */
    lasti = (int)(frame->instr_ptr - 1 - _PyCode_CODE(code));
#elif defined(_PY311_AND_LATER)
    /* Python 3.11-3.12: prev_instr points to the last executed instruction.
     * Result is in _Py_CODEUNIT units. */
    lasti = (int)(frame->prev_instr - _PyCode_CODE(code));
#else
    /* Pre-3.11: f_lasti is a byte offset (3.9) or codeunit index (3.10). */
    lasti = frame->f_lasti;
#endif // _PY313_AND_LATER

    if (lasti < 0) {
        return code->co_firstlineno;
    }

    unsigned int lineno = code->co_firstlineno;

#ifdef _PY311_AND_LATER
    /* Python 3.11+: PEP 657 location table in co_linetable.
     * Each entry byte: bits[2:0] = (codeunit_delta - 1), bits[6:3] = info code.
     * lasti is in _Py_CODEUNIT units, matching the table's bc counter. */
    const unsigned char* table = (const unsigned char*)PyBytes_AS_STRING(code->co_linetable);
    Py_ssize_t len = PyBytes_GET_SIZE(code->co_linetable);

    for (Py_ssize_t i = 0, bc = 0; i < len; i++) {
        bc += (table[i] & 7) + 1;
        int info_code = (table[i] >> 3) & 15;
        switch (info_code) {
            case 15: /* No operation */
                break;
            case 14: /* Long form: signed varint line delta + 3 varints */
                lineno += memalloc_read_signed_varint(table, len, &i);
                memalloc_read_varint(table, len, &i); /* end_line */
                memalloc_read_varint(table, len, &i); /* column */
                memalloc_read_varint(table, len, &i); /* end_column */
                break;
            case 13: /* No column data: signed varint line delta */
                lineno += memalloc_read_signed_varint(table, len, &i);
                break;
            case 12:
            case 11:
            case 10: /* New lineno: delta = info_code - 10, skip 2 column bytes */
                lineno += info_code - 10;
                if (i < len - 2)
                    i += 2;
                break;
            default: /* Same line, skip 1 column byte */
                if (i < len - 1)
                    i += 1;
                break;
        }
        if (bc > lasti)
            break;
    }

#elif defined(_PY310_AND_LATER)
    /* Python 3.10: PEP 626 line table in co_linetable.
     * Pairs of (sdelta, ldelta) bytes.  f_lasti is in codeunit units;
     * the table bytecode deltas are in byte units, so convert. */
    const unsigned char* table = (const unsigned char*)PyBytes_AS_STRING(code->co_linetable);
    Py_ssize_t len = PyBytes_GET_SIZE(code->co_linetable);

    lasti *= (int)sizeof(_Py_CODEUNIT); /* codeunit index → byte offset */
    for (Py_ssize_t i = 0, bc = 0; i < len; i++) {
        int sdelta = table[i++];
        if (sdelta == 0xff)
            break;
        bc += sdelta;
        int ldelta = table[i];
        if (ldelta == 0x80)
            ldelta = 0;
        else if (ldelta > 0x80)
            lineno -= 0x100;
        lineno += ldelta;
        if (bc > lasti)
            break;
    }

#else
    /* Python 3.9: co_lnotab format — pairs of (bytecode_delta, line_delta)
     * unsigned bytes.  f_lasti is a byte offset. */
    const unsigned char* table = (const unsigned char*)PyBytes_AS_STRING(code->co_lnotab);
    Py_ssize_t len = PyBytes_GET_SIZE(code->co_lnotab);

    for (Py_ssize_t i = 0, bc = 0; i < len; i++) {
        bc += table[i++];
        if (bc > lasti)
            break;
        if (table[i] >= 0x80)
            lineno -= 0x100;
        lineno += table[i];
    }

#endif // _PY311_AND_LATER

    return lineno > 0 ? (int)lineno : 0;
}

/* Return the best available function name for a code object.
 * co_qualname (Python 3.11+) provides richer context (e.g., Class.method). */
static inline PyObject*
memalloc_get_code_name(PyCodeObject* code)
{
#ifdef _PY311_AND_LATER
    return code->co_qualname ? code->co_qualname : code->co_name;
#else
    return code->co_name;
#endif // _PY311_AND_LATER
}
