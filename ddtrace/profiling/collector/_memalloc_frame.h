#pragma once

/* Version-specific frame-walking helpers for the memalloc profiler.
 *
 * All helpers use direct struct field reads — no new Python references are
 * created, no Py_INCREF/Py_DECREF, no calls that can allocate or free.
 * This is critical because these are called from inside CPython's
 * PYMEM_DOMAIN_OBJ allocator hook.
 *
 * The GIL is held throughout the allocator hook, so reading internal
 * structures is safe.
 *
 * IMPORTANT: This header must be included AFTER Py_BUILD_CORE is defined
 * and Python.h is included, since the internal headers depend on both.
 */

// Py_BUILD_CORE is required to access CPython internals for direct frame walking.
// Must be defined before Python.h.
#define Py_BUILD_CORE

#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <frameobject.h>

#include "_pymacro.h"

/* Include CPython internal frame headers for zero-refcount frame walking.
 * Python 3.11+: _PyInterpreterFrame is needed for direct frame chain walking.
 * Python 3.14+: definition moved to pycore_interpframe_structs.h
 * Python 3.11-3.13: definition is in pycore_frame.h */
#ifdef _PY311_AND_LATER
#if PY_VERSION_HEX >= 0x030e0000
#include <internal/pycore_interpframe_structs.h>
#else
#include <internal/pycore_frame.h>
#endif
#include <internal/pycore_code.h>
#endif /* _PY311_AND_LATER */

/* Return the innermost interpreter frame from the thread state without
 * incrementing any reference count. Returns a borrowed void* pointer
 * (either _PyInterpreterFrame* on 3.11+ or PyFrameObject* on <3.11). */
static inline void*
memalloc_get_frame_from_thread_state(PyThreadState* tstate)
{
#if PY_VERSION_HEX >= 0x030d0000
    /* Python 3.13+: current_frame is directly on PyThreadState. */
    return (void*)tstate->current_frame;
#elif PY_VERSION_HEX >= 0x030b0000
    /* Python 3.11-3.12: current_frame is on the _PyCFrame. */
    return (void*)tstate->cframe->current_frame;
#else
    /* Pre-3.11: tstate->frame is a public PyFrameObject*. */
    return (void*)tstate->frame;
#endif
}

/* Return the caller's frame (one level up the call stack) without creating
 * a new reference. */
static inline void*
memalloc_get_previous_frame(void* frame)
{
#ifdef _PY311_AND_LATER
    return (void*)((_PyInterpreterFrame*)frame)->previous;
#else
    return (void*)((PyFrameObject*)frame)->f_back;
#endif
}

/* Return the code object for the frame as a borrowed reference (no INCREF).
 * For Python 3.14+, f_executable carries tagged pointer bits that must be
 * masked off before treating it as a PyObject*. */
static inline PyCodeObject*
memalloc_get_code_from_frame(void* frame)
{
#if PY_VERSION_HEX >= 0x030e0000
    /* Python 3.14+: f_executable is a _PyStackRef (tagged pointer).
     * Clear the tag bits to recover the PyObject* pointer.
     * Masking with ~7 (clearing 3 lowest bits) safely covers all configs
     * (debug, free-threading, release), since PyObject* is always aligned
     * to at least 8 bytes. */
    return (PyCodeObject*)((uintptr_t)((_PyInterpreterFrame*)frame)->f_executable.bits & ~(uintptr_t)7);
#elif PY_VERSION_HEX >= 0x030d0000
    /* Python 3.13: f_executable is an untagged PyObject*. */
    return (PyCodeObject*)((_PyInterpreterFrame*)frame)->f_executable;
#elif PY_VERSION_HEX >= 0x030b0000
    /* Python 3.11-3.12: f_code is a direct PyCodeObject*. */
    return ((_PyInterpreterFrame*)frame)->f_code;
#else
    /* Pre-3.11: f_code is a public PyCodeObject*. */
    return ((PyFrameObject*)frame)->f_code;
#endif
}

/* Return true for frames that should be skipped during stack walking:
 *   - frames whose code slot is NULL or not a real PyCodeObject
 *   - Python 3.12+ "cstack" shim frames used during generator/coroutine entry
 *   - Python 3.14+ interpreter-owned shim frames
 *   - Python 3.11 incomplete frames (prev_instr < firsttraceable) */
static inline bool
memalloc_should_skip_frame(void* frame)
{
    PyObject* code = (PyObject*)memalloc_get_code_from_frame(frame);
    if (code == NULL || !PyCode_Check(code)) {
        return true;
    }

#if PY_VERSION_HEX >= 0x030c0000
    _PyInterpreterFrame* iframe = (_PyInterpreterFrame*)frame;
    return iframe->owner != FRAME_OWNED_BY_THREAD && iframe->owner != FRAME_OWNED_BY_GENERATOR;
#elif PY_VERSION_HEX >= 0x030b0000
    return _PyFrame_IsIncomplete((_PyInterpreterFrame*)frame);
#else
    return false;
#endif
}

/* Varint helpers for parsing the 3.11+ location table (PEP 657).
 * These read from the co_linetable byte array — pure byte reads,
 * no allocations or frees. */
#if PY_VERSION_HEX >= 0x030b0000
static inline int
memalloc_read_varint(const unsigned char* table, Py_ssize_t len, Py_ssize_t* i)
{
    Py_ssize_t guard = len - 1;
    if (*i >= guard)
        return 0;
    int val = table[++*i] & 63;
    int shift = 0;
    while (table[*i] & 64 && *i < guard) {
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
#endif /* PY_VERSION_HEX >= 0x030b0000 */

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
 * src/echion/frame.cc) which handles all supported CPython versions. */
static inline int
memalloc_get_lineno(void* frame, PyCodeObject* code)
{
    int lasti;

#if PY_VERSION_HEX >= 0x030d0000
    /* Python 3.13+: instr_ptr points to the NEXT instruction.
     * Result is in _Py_CODEUNIT units. */
    _PyInterpreterFrame* iframe = (_PyInterpreterFrame*)frame;
    lasti = (int)(iframe->instr_ptr - 1 - _PyCode_CODE(code));
#elif PY_VERSION_HEX >= 0x030b0000
    /* Python 3.11-3.12: prev_instr points to the last executed instruction.
     * Result is in _Py_CODEUNIT units. */
    _PyInterpreterFrame* iframe = (_PyInterpreterFrame*)frame;
    lasti = (int)(iframe->prev_instr - _PyCode_CODE(code));
#else
    /* Pre-3.11: f_lasti is a byte offset (3.9) or codeunit index (3.10). */
    lasti = ((PyFrameObject*)frame)->f_lasti;
#endif

    if (lasti < 0) {
        return code->co_firstlineno;
    }

    unsigned int lineno = code->co_firstlineno;

#if PY_VERSION_HEX >= 0x030b0000
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

#elif PY_VERSION_HEX >= 0x030a0000
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

#endif

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
#endif
}
