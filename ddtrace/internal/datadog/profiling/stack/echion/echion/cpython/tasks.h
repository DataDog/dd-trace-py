// This file is part of "echion" which is released under MIT.
//
// Copyright (c) 2023 Gabriele N. Tornetta <phoenix1987@gmail.com>.

#pragma once

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#if PY_VERSION_HEX >= 0x030b0000
#include <cpython/genobject.h>

#define Py_BUILD_CORE
#if PY_VERSION_HEX >= 0x030e0000
#include <cstddef>
#include <internal/pycore_frame.h>
#include <internal/pycore_interpframe.h>
#include <internal/pycore_interpframe_structs.h>
#include <internal/pycore_llist.h>
#include <internal/pycore_stackref.h>
#include <opcode.h>
#elif PY_VERSION_HEX >= 0x030d0000
#include <opcode.h>
#else
#include <cstddef>
#include <internal/pycore_frame.h>
#include <internal/pycore_opcode.h>
#endif // PY_VERSION_HEX >= 0x030d0000
#else
#include <genobject.h>
#include <opcode.h>
#endif

#include <memory>

#include <echion/vm.h>

constexpr ssize_t MAX_STACK_SIZE = 1 << 20; // 1 MiB

extern "C"
{

    typedef enum
    {
        STATE_PENDING,
        STATE_CANCELLED,
        STATE_FINISHED
    } fut_state;

#if PY_VERSION_HEX >= 0x030e0000
// Python 3.14+: New fields added (awaited_by, is_task, awaited_by_is_set)
#define FutureObj_HEAD(prefix)                                                                                         \
    PyObject_HEAD PyObject* prefix##_loop;                                                                             \
    PyObject* prefix##_callback0;                                                                                      \
    PyObject* prefix##_context0;                                                                                       \
    PyObject* prefix##_callbacks;                                                                                      \
    PyObject* prefix##_exception;                                                                                      \
    PyObject* prefix##_exception_tb;                                                                                   \
    PyObject* prefix##_result;                                                                                         \
    PyObject* prefix##_source_tb;                                                                                      \
    PyObject* prefix##_cancel_msg;                                                                                     \
    PyObject* prefix##_cancelled_exc;                                                                                  \
    PyObject* prefix##_awaited_by;                                                                                     \
    fut_state prefix##_state;                                                                                          \
    /* Used by profilers to make traversing the stack from an external                                                 \
       process faster. */                                                                                              \
    char prefix##_is_task;                                                                                             \
    char prefix##_awaited_by_is_set;                                                                                   \
    /* These bitfields need to be at the end of the struct                                                             \
       so that these and bitfields from TaskObj are contiguous.                                                        \
    */                                                                                                                 \
    unsigned prefix##_log_tb : 1;                                                                                      \
    unsigned prefix##_blocking : 1;

#elif PY_VERSION_HEX >= 0x030d0000
#define FutureObj_HEAD(prefix)                                                                                         \
    PyObject_HEAD PyObject* prefix##_loop;                                                                             \
    PyObject* prefix##_callback0;                                                                                      \
    PyObject* prefix##_context0;                                                                                       \
    PyObject* prefix##_callbacks;                                                                                      \
    PyObject* prefix##_exception;                                                                                      \
    PyObject* prefix##_exception_tb;                                                                                   \
    PyObject* prefix##_result;                                                                                         \
    PyObject* prefix##_source_tb;                                                                                      \
    PyObject* prefix##_cancel_msg;                                                                                     \
    PyObject* prefix##_cancelled_exc;                                                                                  \
    fut_state prefix##_state;                                                                                          \
    /* These bitfields need to be at the end of the struct                                                             \
       so that these and bitfields from TaskObj are contiguous.                                                        \
    */                                                                                                                 \
    unsigned prefix##_log_tb : 1;                                                                                      \
    unsigned prefix##_blocking : 1;

#elif PY_VERSION_HEX >= 0x030b0000
#define FutureObj_HEAD(prefix)                                                                                         \
    PyObject_HEAD PyObject* prefix##_loop;                                                                             \
    PyObject* prefix##_callback0;                                                                                      \
    PyObject* prefix##_context0;                                                                                       \
    PyObject* prefix##_callbacks;                                                                                      \
    PyObject* prefix##_exception;                                                                                      \
    PyObject* prefix##_exception_tb;                                                                                   \
    PyObject* prefix##_result;                                                                                         \
    PyObject* prefix##_source_tb;                                                                                      \
    PyObject* prefix##_cancel_msg;                                                                                     \
    fut_state prefix##_state;                                                                                          \
    int prefix##_log_tb;                                                                                               \
    int prefix##_blocking;                                                                                             \
    PyObject* dict;                                                                                                    \
    PyObject* prefix##_weakreflist;                                                                                    \
    PyObject* prefix##_cancelled_exc;

#elif PY_VERSION_HEX >= 0x030a0000
#define FutureObj_HEAD(prefix)                                                                                         \
    PyObject_HEAD PyObject* prefix##_loop;                                                                             \
    PyObject* prefix##_callback0;                                                                                      \
    PyObject* prefix##_context0;                                                                                       \
    PyObject* prefix##_callbacks;                                                                                      \
    PyObject* prefix##_exception;                                                                                      \
    PyObject* prefix##_exception_tb;                                                                                   \
    PyObject* prefix##_result;                                                                                         \
    PyObject* prefix##_source_tb;                                                                                      \
    PyObject* prefix##_cancel_msg;                                                                                     \
    fut_state prefix##_state;                                                                                          \
    int prefix##_log_tb;                                                                                               \
    int prefix##_blocking;                                                                                             \
    PyObject* dict;                                                                                                    \
    PyObject* prefix##_weakreflist;                                                                                    \
    _PyErr_StackItem prefix##_cancelled_exc_state;

#elif PY_VERSION_HEX >= 0x03090000
#define FutureObj_HEAD(prefix)                                                                                         \
    PyObject_HEAD PyObject* prefix##_loop;                                                                             \
    PyObject* prefix##_callback0;                                                                                      \
    PyObject* prefix##_context0;                                                                                       \
    PyObject* prefix##_callbacks;                                                                                      \
    PyObject* prefix##_exception;                                                                                      \
    PyObject* prefix##_result;                                                                                         \
    PyObject* prefix##_source_tb;                                                                                      \
    PyObject* prefix##_cancel_msg;                                                                                     \
    fut_state prefix##_state;                                                                                          \
    int prefix##_log_tb;                                                                                               \
    int prefix##_blocking;                                                                                             \
    PyObject* dict;                                                                                                    \
    PyObject* prefix##_weakreflist;                                                                                    \
    _PyErr_StackItem prefix##_cancelled_exc_state;

#else
#define FutureObj_HEAD(prefix)                                                                                         \
    PyObject_HEAD PyObject* prefix##_loop;                                                                             \
    PyObject* prefix##_callback0;                                                                                      \
    PyObject* prefix##_context0;                                                                                       \
    PyObject* prefix##_callbacks;                                                                                      \
    PyObject* prefix##_exception;                                                                                      \
    PyObject* prefix##_result;                                                                                         \
    PyObject* prefix##_source_tb;                                                                                      \
    fut_state prefix##_state;                                                                                          \
    int prefix##_log_tb;                                                                                               \
    int prefix##_blocking;                                                                                             \
    PyObject* dict;                                                                                                    \
    PyObject* prefix##_weakreflist;
#endif

    typedef struct
    {
        FutureObj_HEAD(future)
    } FutureObj;

#if PY_VERSION_HEX >= 0x030e0000
    // Python 3.14+: TaskObj includes task_node for linked-list storage
    typedef struct
    {
        FutureObj_HEAD(task) unsigned task_must_cancel : 1;
        unsigned task_log_destroy_pending : 1;
        int task_num_cancels_requested;
        PyObject* task_fut_waiter;
        PyObject* task_coro;
        PyObject* task_name;
        PyObject* task_context;
        struct llist_node task_node;
#ifdef Py_GIL_DISABLED
        // thread id of the thread where this task was created
        uintptr_t task_tid;
#endif
    } TaskObj;
#elif PY_VERSION_HEX >= 0x030d0000
    typedef struct
    {
        FutureObj_HEAD(task) unsigned task_must_cancel : 1;
        unsigned task_log_destroy_pending : 1;
        int task_num_cancels_requested;
        PyObject* task_fut_waiter;
        PyObject* task_coro;
        PyObject* task_name;
        PyObject* task_context;
    } TaskObj;

#elif PY_VERSION_HEX >= 0x030a0000
    typedef struct
    {
        FutureObj_HEAD(task) PyObject* task_fut_waiter;
        PyObject* task_coro;
        PyObject* task_name;
        PyObject* task_context;
        int task_must_cancel;
        int task_log_destroy_pending;
        int task_num_cancels_requested;
    } TaskObj;

#else
    typedef struct
    {
        FutureObj_HEAD(task) PyObject* task_fut_waiter;
        PyObject* task_coro;
        PyObject* task_name;
        PyObject* task_context;
        int task_must_cancel;
        int task_log_destroy_pending;
    } TaskObj;
#endif

    // ---- cr_await ----

#if PY_VERSION_HEX >= 0x030c0000
#define RESUME_QUICK INSTRUMENTED_RESUME
#endif

#if PY_VERSION_HEX >= 0x030e0000
    // Python 3.14+: Use stackpointer and _PyStackRef

    inline PyObject* PyGen_yf(PyGenObject* gen, PyObject* frame_addr)
    {
        if (gen->gi_frame_state != FRAME_SUSPENDED_YIELD_FROM) {
            return nullptr;
        }

        _PyInterpreterFrame frame;
        if (copy_type(frame_addr, frame)) {
            return nullptr;
        }

        // CPython asserts the following:
        // assert(f->stackpointer >  f->localsplus + _PyFrame_GetCode(f)->co_nlocalsplus);
        // assert(!PyStackRef_IsNull(f->stackpointer[-1]));

        // Though we have to pay the price of copying the code object, we need
        // to do this to catch the case where the stack is empty, as accessing
        // frame.stackpointer[-1] would be an undefined behavior.
        // This is necessary as frame.stacktop is removed in 3.14.
        PyCodeObject code;
        auto code_addr = reinterpret_cast<PyCodeObject*>(BITS_TO_PTR_MASKED(frame.f_executable));
        if (copy_type(code_addr, code)) {
            return nullptr;
        }

        uintptr_t frame_addr_uint = reinterpret_cast<uintptr_t>(frame_addr);
        uintptr_t localsplus_addr = frame_addr_uint + offsetof(_PyInterpreterFrame, localsplus);
        // This computes f->localsplus + code.co_nlocalsplus.
        uintptr_t stackbase_addr = localsplus_addr + code.co_nlocalsplus * sizeof(_PyStackRef);

        uintptr_t stackpointer_addr = reinterpret_cast<uintptr_t>(frame.stackpointer);
        // We want stackpointer_addr to be greater than the stackbase_addr,
        // that is, the stack is not empty.
        if (stackpointer_addr <= stackbase_addr) {
            return nullptr;
        }

        // We can also calculate stacktop and check that it is within a reasonable range.
        // Similar to 3.13's stacktop check below.
        int stacktop = (int)((stackpointer_addr - stackbase_addr) / sizeof(_PyStackRef));

        if (stacktop < 1 || stacktop > MAX_STACK_SIZE) {
            return nullptr;
        }

        // Read the top of stack directly from remote memory
        // This is equivalent to CPython's frame.stackpointer[-1].
        _PyStackRef top_ref;
        if (copy_type(reinterpret_cast<void*>(stackpointer_addr - sizeof(_PyStackRef)), top_ref)) {
            return nullptr;
        }

        // Extract PyObject* from _PyStackRef.bits
        // Per Python 3.14 release notes (gh-123923): clear LSB to recover PyObject* pointer
        return BITS_TO_PTR_MASKED(top_ref);
    }

#elif PY_VERSION_HEX >= 0x030d0000

    inline PyObject* PyGen_yf(PyGenObject* gen, PyObject* frame_addr)
    {
        if (gen->gi_frame_state != FRAME_SUSPENDED_YIELD_FROM) {
            return nullptr;
        }

        _PyInterpreterFrame frame;
        if (copy_type(frame_addr, frame)) {
            return nullptr;
        }

        if (frame.stacktop < 1 || frame.stacktop > MAX_STACK_SIZE) {
            return nullptr;
        }

        auto localsplus = std::make_unique<PyObject*[]>(frame.stacktop);

        // Calculate the remote address of the localsplus array
        auto remote_localsplus = reinterpret_cast<PyObject**>(reinterpret_cast<uintptr_t>(frame_addr) +
                                                              offsetof(_PyInterpreterFrame, localsplus));
        if (copy_generic(remote_localsplus, localsplus.get(), frame.stacktop * sizeof(PyObject*))) {
            return nullptr;
        }

        return localsplus[frame.stacktop - 1];
    }

#elif PY_VERSION_HEX >= 0x030b0000

    inline PyObject* PyGen_yf(PyGenObject* gen, PyObject* frame_addr)
    {
        PyObject* yf = NULL;

        if (gen->gi_frame_state < FRAME_CLEARED) {
            if (gen->gi_frame_state == FRAME_CREATED)
                return NULL;

            _PyInterpreterFrame frame;
            if (copy_type(frame_addr, frame))
                return NULL;

            _Py_CODEUNIT next;
#if PY_VERSION_HEX >= 0x030d0000
            if (copy_type(frame.instr_ptr, next))
#else
            if (copy_type(frame.prev_instr + 1, next))
#endif
                return NULL;
            if (!(_Py_OPCODE(next) == RESUME || _Py_OPCODE(next) == RESUME_QUICK) || _Py_OPARG(next) < 2)
                return NULL;

            if (frame.stacktop < 1 || frame.stacktop > MAX_STACK_SIZE)
                return NULL;

            auto localsplus = std::make_unique<PyObject*[]>(frame.stacktop);
            // Calculate the remote address of the localsplus array
            auto localsplus_addr = reinterpret_cast<uintptr_t>(frame_addr) + offsetof(_PyInterpreterFrame, localsplus);
            auto remote_localsplus = reinterpret_cast<PyObject**>(localsplus_addr);
            if (copy_generic(remote_localsplus, localsplus.get(), frame.stacktop * sizeof(PyObject*))) {
                return NULL;
            }

            yf = localsplus[frame.stacktop - 1];
        }

        return yf;
    }

#elif PY_VERSION_HEX >= 0x030a0000
    inline PyObject* PyGen_yf(PyGenObject* Py_UNUSED(gen), PyObject* frame_addr)
    {
        PyObject* yf = NULL;
        PyFrameObject* f = (PyFrameObject*)frame_addr;

        if (f) {
            PyFrameObject frame;
            if (copy_type(f, frame))
                return NULL;

            if (frame.f_lasti < 0)
                return NULL;

            PyCodeObject code;
            if (copy_type(frame.f_code, code))
                return NULL;

            Py_ssize_t s = 0;
            auto c = pybytes_to_bytes_and_size(code.co_code, &s);
            if (c == nullptr)
                return NULL;

            if (c[(frame.f_lasti + 1) * sizeof(_Py_CODEUNIT)] != YIELD_FROM)
                return NULL;

            ssize_t nvalues = frame.f_stackdepth;
            if (nvalues < 1 || nvalues > MAX_STACK_SIZE)
                return NULL;

            auto stack = std::make_unique<PyObject*[]>(nvalues);

            if (copy_generic(frame.f_valuestack, stack.get(), nvalues * sizeof(PyObject*)))
                return NULL;

            yf = stack[nvalues - 1];
        }

        return yf;
    }

#else
    inline PyObject* PyGen_yf(PyGenObject* Py_UNUSED(gen), PyObject* frame_addr)
    {
        PyObject* yf = NULL;
        PyFrameObject* f = (PyFrameObject*)frame_addr;

        if (frame_addr == NULL)
            return NULL;

        PyFrameObject frame;
        if (copy_type(f, frame))
            return NULL;

        if (frame.f_stacktop) {
            if (frame.f_lasti < 0)
                return NULL;

            PyCodeObject code;
            if (copy_type(frame.f_code, code))
                return NULL;

            Py_ssize_t s = 0;
            auto c = pybytes_to_bytes_and_size(code.co_code, &s);
            if (c == nullptr)
                return NULL;

            if (c[f->f_lasti + sizeof(_Py_CODEUNIT)] != YIELD_FROM)
                return NULL;

            auto stacktop = std::make_unique<PyObject*>();
            if (copy_generic(frame.f_stacktop - 1, stacktop.get(), sizeof(PyObject*)))
                return NULL;

            yf = *stacktop;
        }

        return yf;
    }
#endif
}
