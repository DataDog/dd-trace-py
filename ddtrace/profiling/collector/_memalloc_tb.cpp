/* Py_BUILD_CORE must be defined before Python.h to access internal CPython
 * frame structures (_PyInterpreterFrame). This is required for the zero-refcount
 * frame walking path on Python 3.12+, which avoids Py_DECREF inside the
 * allocator hook to prevent re-entering the allocator's free path. */
#define Py_BUILD_CORE

#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <frameobject.h>
#include <string_view>

#include "_pymacro.h"

/* Include CPython internal frame headers for zero-refcount frame walking.
 * Python 3.14+: _PyInterpreterFrame definition moved to pycore_interpframe_structs.h
 * Python 3.12-3.13: _PyInterpreterFrame is in pycore_frame.h */
#ifdef _PY312_AND_LATER
#if PY_VERSION_HEX >= 0x030e0000
#include <internal/pycore_interpframe_structs.h>
#else
#include <internal/pycore_frame.h>
#endif
#include <internal/pycore_code.h>
#endif /* _PY312_AND_LATER */

#include "_memalloc_debug.h"
#include "_memalloc_reentrant.h"
#include "_memalloc_tb.h"
#include "python_helpers.hpp"
/* Helper function to get thread info using C-level APIs and push to sample.
 *
 * Uses only PyThread_get_thread_ident() and PyThread_get_thread_native_id(),
 * which are C-level calls that never re-enter the Python interpreter.
 * This is critical because this function is called from inside CPython's
 * PYMEM_DOMAIN_OBJ allocation path. Calling Python-level code (e.g.,
 * threading.current_thread()) from here can release the GIL via the eval
 * loop's periodic check, allowing other threads to observe partially-constructed
 * CPython internal state and causing crashes.
 *
 * Thread names are not available from C-level APIs; push_threadinfo falls back
 * to str(thread_id) when name is empty. The stack profiler provides thread names
 * via stack.register_thread() at safe points.
 */
static void
push_threadinfo_to_sample(Datadog::Sample& sample)
{
    int64_t thread_id = (int64_t)PyThread_get_thread_ident();
    if (thread_id == 0) {
        return;
    }

    int64_t thread_native_id = (int64_t)PyThread_get_thread_native_id();

    // Pass empty name; push_threadinfo will fall back to str(thread_id)
    sample.push_threadinfo(thread_id, thread_native_id, "");
}

#ifdef _PY312_AND_LATER
/* Walk the _PyInterpreterFrame chain directly without creating any new
 * Python object references.
 *
 * CRITICAL: This function is called from inside CPython's PYMEM_DOMAIN_OBJ
 * allocation hook. The previous implementation used PyFrame_GetCode() and
 * PyFrame_GetBack(), which return NEW references that must be Py_DECREF'd.
 * Those Py_DECREF calls can trigger _Py_Dealloc, which re-enters the
 * allocator via memalloc_free -> memalloc_heap_untrack, corrupting the
 * heap tracker's hash map or pymalloc's arena metadata.
 *
 * By accessing _PyInterpreterFrame fields directly, we use only BORROWED
 * references. No Py_INCREF, no Py_DECREF, no possibility of re-entering
 * the allocator through reference counting.
 *
 * Field access by Python version:
 *   3.12:  f_code (PyCodeObject*), prev_instr, previous, owner
 *   3.13:  f_executable (PyObject*), instr_ptr, previous, owner
 *   3.14+: f_executable (tagged PyObject*; clear LSB), instr_ptr, previous, owner
 */
static void
push_stacktrace_to_sample_no_decref(Datadog::Sample& sample)
{
    PyThreadState* tstate = PyThreadState_Get();
    if (tstate == NULL) {
        sample.push_frame("<no thread state>", "<unknown>", 0, 0);
        return;
    }

#if PY_VERSION_HEX >= 0x030d0000
    /* Python 3.13+: current_frame is directly on PyThreadState. */
    _PyInterpreterFrame* iframe = tstate->current_frame;
#else
    /* Python 3.12: current_frame is on the _PyCFrame, not PyThreadState. */
    _PyInterpreterFrame* iframe = tstate->cframe->current_frame;
#endif
    if (iframe == NULL) {
        sample.push_frame("<no Python frames>", "<unknown>", 0, 0);
        return;
    }

    for (; iframe != NULL; iframe = iframe->previous) {
        /* Skip incomplete frames (C shim frames on 3.12+, interpreter frames
         * on 3.13+). These don't have valid Python code objects.
         * _PyFrame_IsIncomplete checks owner >= FRAME_OWNED_BY_CSTACK. */
        if (iframe->owner >= FRAME_OWNED_BY_CSTACK) {
            continue;
        }

        /* Get code object — borrowed reference, no INCREF needed. */
#if PY_VERSION_HEX >= 0x030e0000
        /* Python 3.14+: f_executable uses tagged pointers (LSB set).
         * Clear the tag bit to recover the PyObject* pointer. */
        PyCodeObject* code = (PyCodeObject*)((uintptr_t)iframe->f_executable & ~(uintptr_t)1);
#elif PY_VERSION_HEX >= 0x030d0000
        /* Python 3.13: f_executable is an untagged PyObject*. */
        PyCodeObject* code = (PyCodeObject*)iframe->f_executable;
#else
        /* Python 3.12: f_code is a direct PyCodeObject*. */
        PyCodeObject* code = iframe->f_code;
#endif

        if (code == NULL) {
            continue;
        }

        /* Extract function name — borrowed reference from code object.
         * co_qualname (3.11+) gives qualified names like "Class.method". */
        std::string_view name_sv = "<unknown>";
        PyObject* name_obj = code->co_qualname ? code->co_qualname : code->co_name;
        if (name_obj != NULL) {
            Py_ssize_t len;
            const char* ptr = PyUnicode_AsUTF8AndSize(name_obj, &len);
            if (ptr) {
                name_sv = std::string_view(ptr, len);
            } else {
                PyErr_Clear();
            }
        }

        /* Extract filename — borrowed reference from code object. */
        std::string_view filename_sv = "<unknown>";
        if (code->co_filename != NULL) {
            Py_ssize_t len;
            const char* ptr = PyUnicode_AsUTF8AndSize(code->co_filename, &len);
            if (ptr) {
                filename_sv = std::string_view(ptr, len);
            } else {
                PyErr_Clear();
            }
        }

        /* Compute line number from instruction pointer offset.
         * _PyCode_CODE(code) gives the start of the bytecode array.
         * PyCode_Addr2Line takes a byte offset (not instruction index). */
#if PY_VERSION_HEX >= 0x030d0000
        /* Python 3.13+: instr_ptr points to the NEXT instruction.
         * Subtract 1 to get the last executed instruction. */
        int lasti = (int)(iframe->instr_ptr - 1 - _PyCode_CODE(code));
#else
        /* Python 3.12: prev_instr points to the last executed instruction. */
        int lasti = (int)(iframe->prev_instr - _PyCode_CODE(code));
#endif
        int line = PyCode_Addr2Line(code, lasti * (int)sizeof(_Py_CODEUNIT));
        if (line < 0) {
            line = 0;
        }

        /* push_frame handles the max_nframe limit internally and tracks
         * dropped frames. It copies strings into its StringArena immediately,
         * so the borrowed string_views are safe here. */
        sample.push_frame(name_sv, filename_sv, 0, line);
    }
}
#endif /* _PY312_AND_LATER */

#ifndef _PY312_AND_LATER
/* Helper function to collect frames from PyFrameObject chain and push to sample
 * Uses Sample::push_pyframes for the actual frame unwinding */
static void
push_stacktrace_to_sample_invokes_cpython(Datadog::Sample& sample)
{
    PyThreadState* tstate = PyThreadState_Get();
    if (tstate == NULL) {
        // Push a placeholder frame when thread state is unavailable
        sample.push_frame("<no thread state>", "<unknown>", 0, 0);
        return;
    }

    // Python 3.9+: PyThreadState_GetFrame() returns a new reference
    PyFrameObject* pyframe = PyThreadState_GetFrame(tstate);

    if (pyframe == NULL) {
        // No Python frames available (e.g., during thread initialization/cleanup in "Dummy" threads).
        // This occurs in Python 3.10-3.12 but not in 3.13+ due to threading implementation changes.
        //
        // The previous implementation (before dd_wrapper Sample refactor) dropped these samples entirely
        // by returning NULL from traceback_new(). This new approach is strictly better: we still capture
        // allocation metrics and make it explicit in profiles that the stack wasn't available.
        //
        // TODO(profiling): Investigate if there's a way to capture C-level stack traces or other context
        // when Python frames aren't available during thread initialization/cleanup.
        sample.push_frame("<no Python frames>", "<unknown>", 0, 0);
        return;
    }

    // Use the unified frame unwinding API
    // Note: push_pyframes does not take ownership of the initial frame, so we must DECREF it here
    sample.push_pyframes(pyframe);
    Py_DECREF(pyframe);
}
#endif /* !_PY312_AND_LATER */

void
traceback_t::init_sample_invokes_cpython(size_t size, size_t weighted_size)
{
    // Preserve any raised C-level exception already in flight before touching
    // CPython C-API from inside the allocator hook.
    PythonErrorRestorer error_restorer;

    // Size 0 allocations are legal and we can hypothetically sample them,
    // e.g. if an allocation during sampling pushes us over the next sampling threshold,
    // but we can't sample it, so we sample the next allocation which happens to be 0
    // bytes. Defensively make sure size isn't 0.
    size_t adjusted_size = size > 0 ? size : 1;
    double scaled_count = ((double)weighted_size) / ((double)adjusted_size);
    size_t count = (size_t)scaled_count;

    // Push allocation info to sample
    // Note: profile_state is initialized in memalloc_start() before any traceback_t objects are created
    sample.push_alloc(weighted_size, count);

    // Get thread id and native_id using C-level APIs and push to sample
    push_threadinfo_to_sample(sample);

    // Collect frames from the Python frame chain and push to Sample
    // Note: Sample.push_frame() automatically enforces the max_nframe limit and tracks dropped frames.
#ifdef _PY312_AND_LATER
    // On Python 3.12+, walk _PyInterpreterFrame directly to avoid all
    // Py_DECREF calls inside the allocator hook. Py_DECREF can trigger
    // _Py_Dealloc which re-enters the allocator via memalloc_free,
    // potentially corrupting the heap tracker or pymalloc arenas.
    push_stacktrace_to_sample_no_decref(sample);
#else
    push_stacktrace_to_sample_invokes_cpython(sample);
#endif
}

// AIDEV-NOTE: Constructor invokes CPython APIs via init_sample_invokes_cpython()
traceback_t::traceback_t(size_t size, size_t weighted_size, uint16_t max_nframe)
  : sample(static_cast<Datadog::SampleType>(Datadog::SampleType::Allocation | Datadog::SampleType::Heap), max_nframe)
{
    // Validate Sample object is in a valid state before use
    if (max_nframe == 0) {
        // Should not happen, but defensive check
        return;
    }

    init_sample_invokes_cpython(size, weighted_size);
}
