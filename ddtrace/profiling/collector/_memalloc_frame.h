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

#if PY_VERSION_HEX >= 0x030e0000
    _PyInterpreterFrame* iframe = (_PyInterpreterFrame*)frame;
    return iframe->owner != FRAME_OWNED_BY_THREAD && iframe->owner != FRAME_OWNED_BY_GENERATOR;
#elif PY_VERSION_HEX >= 0x030c0000
    _PyInterpreterFrame* iframe = (_PyInterpreterFrame*)frame;
    return iframe->owner >= FRAME_OWNED_BY_CSTACK;
#elif PY_VERSION_HEX >= 0x030b0000
    return _PyFrame_IsIncomplete((_PyInterpreterFrame*)frame);
#else
    return false;
#endif
}

/* Return the current line number for the frame.
 *
 * Computes the byte offset of the last executed instruction and passes it
 * to PyCode_Addr2Line(), which is pure line-table parsing — no allocation,
 * no error state mutation, safe inside allocator hooks.
 *
 * On 3.11+ the instruction pointer is an index into the _Py_CODEUNIT array,
 * so we multiply by sizeof(_Py_CODEUNIT) to get a byte offset.
 * On pre-3.11, f_lasti is already a byte offset. */
static inline int
memalloc_get_lineno(void* frame, PyCodeObject* code)
{
    int lasti;

#if PY_VERSION_HEX >= 0x030d0000
    /* Python 3.13+: instr_ptr points to the NEXT instruction. */
    _PyInterpreterFrame* iframe = (_PyInterpreterFrame*)frame;
    lasti = (int)(iframe->instr_ptr - 1 - _PyCode_CODE(code)) * (int)sizeof(_Py_CODEUNIT);
#elif PY_VERSION_HEX >= 0x030b0000
    /* Python 3.11-3.12: prev_instr points to the last executed instruction. */
    _PyInterpreterFrame* iframe = (_PyInterpreterFrame*)frame;
    lasti = (int)(iframe->prev_instr - _PyCode_CODE(code)) * (int)sizeof(_Py_CODEUNIT);
#else
    /* Pre-3.11: f_lasti is already a byte offset. */
    lasti = ((PyFrameObject*)frame)->f_lasti;
#endif

    int line = PyCode_Addr2Line(code, lasti);
    return line >= 0 ? line : 0;
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
