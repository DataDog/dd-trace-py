#pragma once

// Py_BUILD_CORE is required to access CPython internals for direct frame walking.
// Must be defined before Python.h. See _memalloc_frame.h for details.
#define Py_BUILD_CORE

#include <Python.h>
#include <frameobject.h>

// Disable MSVC warning about CPython internal header syntax
#ifdef _MSC_VER
#pragma warning(push)
#pragma warning(disable : 4576)
#endif

#if PY_VERSION_HEX >= 0x030b0000
#include <cstddef>
#include <internal/pycore_code.h>
#include <internal/pycore_frame.h>
#if PY_VERSION_HEX >= 0x030e0000
#include <internal/pycore_interpframe.h>
#include <internal/pycore_interpframe_structs.h>
#include <internal/pycore_stackref.h>
#endif // PY_VERSION_HEX >= 0x030e0000
#endif // PY_VERSION_HEX >= 0x030b0000

#ifdef _MSC_VER
#pragma warning(pop)
#endif

// Fast frame-walking helpers for the memalloc profiler.
//
// AIDEV-NOTE: Mirrors ddtrace/internal/utils/_inspection.cpp (PR #16430) but
// integrated directly with Sample::push_frame() to avoid building Python list
// objects.
//
// Key difference from push_pyframes() in sample.cpp:
//   - No new Python references are created while traversing the frame chain.
//   - PyThreadState_GetFrame(), PyFrame_GetBack(), PyFrame_GetCode() are all
//     skipped in favour of direct struct field reads.
//   - This is safe because the GIL is held throughout the allocator hook and
//     we only read, never mutate, these internal structures.
//
// IMPORTANT: This header must be included AFTER Python.h has been included
// with Py_BUILD_CORE defined, and after the conditional internal CPython
// headers (pycore_frame.h etc.) have been included.  See _memalloc_tb.cpp.

// Return the innermost interpreter frame from the thread state without
// incrementing any reference count.
static inline void*
memalloc_get_frame_from_thread_state(PyThreadState* tstate)
{
#if PY_VERSION_HEX >= 0x030d0000
    return reinterpret_cast<void*>(tstate->current_frame);
#elif PY_VERSION_HEX >= 0x030b0000
    return reinterpret_cast<void*>(tstate->cframe->current_frame);
#else
    return reinterpret_cast<void*>(tstate->frame);
#endif
}

// Return the caller's frame (one level up the call stack) without creating
// a new reference.
static inline void*
memalloc_get_previous_frame(void* frame_like)
{
#if PY_VERSION_HEX >= 0x030b0000
    return reinterpret_cast<void*>(reinterpret_cast<_PyInterpreterFrame*>(frame_like)->previous);
#else
    return reinterpret_cast<void*>(reinterpret_cast<PyFrameObject*>(frame_like)->f_back);
#endif
}

// Return the code object for the frame as a borrowed reference (no INCREF).
// For Python 3.14+, f_executable carries tagged pointer bits that must be
// masked off via BITS_TO_PTR_MASKED before treating it as a PyObject*.
static inline PyCodeObject*
memalloc_get_code_from_frame(void* frame_like)
{
#if PY_VERSION_HEX >= 0x030e0000
    _PyInterpreterFrame* py_frame = reinterpret_cast<_PyInterpreterFrame*>(frame_like);
    return reinterpret_cast<PyCodeObject*>(BITS_TO_PTR_MASKED(py_frame->f_executable));
#elif PY_VERSION_HEX >= 0x030d0000
    _PyInterpreterFrame* py_frame = reinterpret_cast<_PyInterpreterFrame*>(frame_like);
    return reinterpret_cast<PyCodeObject*>(py_frame->f_executable);
#elif PY_VERSION_HEX >= 0x030b0000
    _PyInterpreterFrame* py_frame = reinterpret_cast<_PyInterpreterFrame*>(frame_like);
    return py_frame->f_code;
#else
    PyFrameObject* py_frame = reinterpret_cast<PyFrameObject*>(frame_like);
    return py_frame->f_code;
#endif
}

// Return true for frames that should be skipped:
//   - frames whose code slot is not a real PyCodeObject (C/shim frames)
//   - Python 3.12+ "cstack" shim frames used during generator/coroutine entry
//   - Python 3.14+ interpreter-owned shim frames
static inline bool
memalloc_should_skip_frame(void* frame_like)
{
    PyObject* code = reinterpret_cast<PyObject*>(memalloc_get_code_from_frame(frame_like));
    if (code == NULL || !PyCode_Check(code)) {
        return true;
    }
#if PY_VERSION_HEX >= 0x030e0000
    _PyInterpreterFrame* py_frame = reinterpret_cast<_PyInterpreterFrame*>(frame_like);
    return py_frame->owner & (FRAME_OWNED_BY_CSTACK | FRAME_OWNED_BY_INTERPRETER);
#elif PY_VERSION_HEX >= 0x030c0000
    _PyInterpreterFrame* py_frame = reinterpret_cast<_PyInterpreterFrame*>(frame_like);
    return py_frame->owner & FRAME_OWNED_BY_CSTACK;
#else
    return false;
#endif
}

// Return the "last instruction" byte offset for the frame.
// Used together with the code object's linetable to resolve the current line.
static inline int
memalloc_get_lasti_from_frame(void* frame_like)
{
#if PY_VERSION_HEX >= 0x030b0000
    return _PyInterpreterFrame_LASTI(reinterpret_cast<_PyInterpreterFrame*>(frame_like));
#else
    return reinterpret_cast<PyFrameObject*>(frame_like)->f_lasti;
#endif
}

#if PY_VERSION_HEX >= 0x030b0000
// Decode an unsigned variable-length integer from the linetable.
// The encoding matches CPython's internal "co_linetable" format (Python 3.11+).
static inline int
memalloc_read_varint(unsigned char* table, Py_ssize_t size, Py_ssize_t* i)
{
    Py_ssize_t guard = size - 1;
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
memalloc_read_signed_varint(unsigned char* table, Py_ssize_t size, Py_ssize_t* i)
{
    int val = memalloc_read_varint(table, size, i);
    return (val & 1) ? -(val >> 1) : (val >> 1);
}
#endif // PY_VERSION_HEX >= 0x030b0000

// Resolve the current line number from a code object and the last-instruction
// offset, without invoking any Python-level API that might allocate.
// Returns co_firstlineno on failure.
static inline int
memalloc_get_lineno_from_code(PyCodeObject* code_obj, int lasti)
{
    unsigned int lineno = code_obj->co_firstlineno;
    Py_ssize_t len = 0;
    unsigned char* table = nullptr;

#if PY_VERSION_HEX >= 0x030b0000
    // Python 3.11+: new linetable format stored in co_linetable
    if (PyBytes_AsStringAndSize(code_obj->co_linetable, (char**)&table, &len) == -1) {
        PyErr_Clear();
        return static_cast<int>(lineno);
    }

    for (Py_ssize_t i = 0, bc = 0; i < len; i++) {
        bc += (table[i] & 7) + 1;
        int code = (table[i] >> 3) & 15;
        switch (code) {
            case 15:
                break;

            case 14: // Long form
                lineno += memalloc_read_signed_varint(table, len, &i);
                (void)memalloc_read_varint(table, len, &i); // line_end delta
                (void)memalloc_read_varint(table, len, &i); // column
                (void)memalloc_read_varint(table, len, &i); // column_end
                break;

            case 13: // No column data
                lineno += memalloc_read_signed_varint(table, len, &i);
                break;

            case 12: // New lineno (delta 2)
            case 11: // New lineno (delta 1)
            case 10: // New lineno (delta 0)
                lineno += code - 10;
                i += 2; // skip column start/end bytes
                break;

            default:
                i += 1; // skip next byte
                break;
        }

        if (bc > lasti)
            break;
    }

#elif PY_VERSION_HEX >= 0x030a0000
    // Python 3.10: co_linetable uses a different (simpler) format
    if (PyBytes_AsStringAndSize(code_obj->co_linetable, (char**)&table, &len) == -1) {
        PyErr_Clear();
        return static_cast<int>(lineno);
    }

    lasti <<= 1;
    for (int i = 0, bc = 0; i < len; i++) {
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
    // Python < 3.10: classic co_lnotab
    if (PyBytes_AsStringAndSize(code_obj->co_lnotab, (char**)&table, &len) == -1) {
        PyErr_Clear();
        return static_cast<int>(lineno);
    }

    for (int i = 0, bc = 0; i < len; i++) {
        bc += table[i++];
        if (bc > lasti)
            break;
        if (table[i] >= 0x80)
            lineno -= 0x100;
        lineno += table[i];
    }
#endif

    return static_cast<int>(lineno);
}

// Return the best available function name for a code object.
// co_qualname (Python 3.11+) provides richer context (e.g., Class.method).
static inline PyObject*
memalloc_get_code_name(PyCodeObject* code_obj)
{
#if PY_VERSION_HEX >= 0x030b0000
    return code_obj->co_qualname ? code_obj->co_qualname : code_obj->co_name;
#else
    return code_obj->co_name;
#endif
}
