#pragma once

/* Shared frame access utilities for dd-trace-py profiling.
 *
 * Version-specific helpers for reading Python frame objects without
 * creating new references or allocating memory.
 *
 * Safe for both:
 *   - memalloc's allocator hook (no allocations, no refcount ops)
 *   - echion's sampling thread (no GIL assumption, pure struct reads)
 *
 * All functions operate on locally-held struct data.  Consumers are
 * responsible for obtaining valid struct data:
 *   - memalloc reads directly with the GIL held
 *   - echion uses copy_type() for cross-thread reads
 *
 * Note: should_skip_frame() calls PyCode_Check(), which dereferences the
 * code object's ob_type pointer.  This is safe when the GIL is held
 * (memalloc) but may not be safe for cross-thread reads without additional
 * precautions.
 */

#include "version_compat.h"

#include <cstdint>

namespace DataDog {

/* Return the innermost interpreter frame from the thread state.
 * Returns a borrowed frame pointer (no reference created). */
inline py_frame_t*
get_frame_from_tstate(PyThreadState* tstate)
{
#if PY_VERSION_HEX >= 0x030d0000
    /* Python 3.13+: current_frame is directly on PyThreadState. */
    return tstate->current_frame;
#elif PY_VERSION_HEX >= 0x030b0000
    /* Python 3.11-3.12: current_frame is on the _PyCFrame.
     * cframe can be NULL while a thread is still being initialized. */
    return tstate->cframe ? tstate->cframe->current_frame : NULL;
#else
    /* Pre-3.11: tstate->frame is a public PyFrameObject*. */
    return tstate->frame;
#endif
}

/* Return the caller's frame (one level up the call stack) without
 * creating a new reference. */
inline py_frame_t*
get_previous_frame(py_frame_t* frame)
{
#if PY_VERSION_HEX >= 0x030b0000
    return frame->previous;
#else
    return frame->f_back;
#endif
}

/* Return the code object for the frame as a borrowed reference.
 * For Python 3.14+, f_executable carries tagged pointer bits that
 * must be masked off before treating it as a PyObject*. */
inline PyCodeObject*
get_code_from_frame(py_frame_t* frame)
{
#if PY_VERSION_HEX >= 0x030e0000
    /* Python 3.14+: f_executable is a _PyStackRef (tagged pointer).
     * Clear the tag bits to recover the PyObject* pointer.
     * Masking with ~7 (clearing 3 lowest bits) safely covers all
     * configs (debug, free-threading, release), since PyObject* is
     * always aligned to at least 8 bytes. */
    return reinterpret_cast<PyCodeObject*>(static_cast<uintptr_t>(frame->f_executable.bits) &
                                           ~static_cast<uintptr_t>(7));
#elif PY_VERSION_HEX >= 0x030d0000
    /* Python 3.13: f_executable is an untagged PyObject*. */
    return reinterpret_cast<PyCodeObject*>(frame->f_executable);
#else
    /* Python 3.9-3.12: f_code is a direct PyCodeObject*. */
    return frame->f_code;
#endif
}

/* Return true for frames that should be skipped during stack walking:
 *   - frames whose code slot is NULL or not a real PyCodeObject
 *   - Python 3.12+ "cstack"/interpreter shim frames
 *   - Python 3.11 incomplete frames (prev_instr < firsttraceable)
 *
 * IMPORTANT: Calls PyCode_Check() which dereferences the code object.
 * Only safe when the code object pointer is valid (GIL held or
 * object known to be alive). */
inline bool
should_skip_frame(py_frame_t* frame)
{
    PyObject* code = reinterpret_cast<PyObject*>(get_code_from_frame(frame));
    if (code == NULL || !PyCode_Check(code)) {
        return true;
    }

#if PY_VERSION_HEX >= 0x030c0000
    return frame->owner != FRAME_OWNED_BY_THREAD && frame->owner != FRAME_OWNED_BY_GENERATOR;
#elif PY_VERSION_HEX >= 0x030b0000
    return _PyFrame_IsIncomplete(frame);
#else
    return false;
#endif
}

/* Return the best available function name for a code object.
 * co_qualname (Python 3.11+) provides richer context (e.g., Class.method).
 * Returns a borrowed reference. */
inline PyObject*
get_code_name(PyCodeObject* code)
{
#if PY_VERSION_HEX >= 0x030b0000
    return code->co_qualname ? code->co_qualname : code->co_name;
#else
    return code->co_name;
#endif
}

/* Compute the last-instruction index (lasti) from a frame and its
 * code object.  The result is suitable for passing to parse_linetable().
 *
 * Requires: code must be a valid, accessible PyCodeObject* (not null, not a
 * remote address).  On Python 3.11+, _PyCode_CODE(code) is dereferenced to
 * compute the pointer difference; passing a dangling or remote pointer is UB.
 * The [[maybe_unused]] annotation suppresses the unused-parameter warning on
 * pre-3.11 builds where f_lasti is read directly from the frame.
 *
 * Version semantics:
 *   Python 3.13+: instr_ptr points to the CURRENT instruction; result
 *                 is in _Py_CODEUNIT units.  No -1 adjustment needed.
 *   Python 3.11-3.12: prev_instr points to the last executed
 *                     instruction; result is in _Py_CODEUNIT units.
 *   Pre-3.11: f_lasti is a byte offset (3.9) or codeunit index (3.10). */
inline int
get_lasti(py_frame_t* frame, [[maybe_unused]] PyCodeObject* code)
{
#if PY_VERSION_HEX >= 0x030d0000
    return static_cast<int>(frame->instr_ptr - _PyCode_CODE(code));
#elif PY_VERSION_HEX >= 0x030b0000
    return static_cast<int>(frame->prev_instr - _PyCode_CODE(code));
#else
    return frame->f_lasti;
#endif
}

} /* namespace DataDog */
