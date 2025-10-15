// This file is part of "echion" which is released under MIT.
//
// Copyright (c) 2023 Gabriele N. Tornetta <phoenix1987@gmail.com>.

#pragma once

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#if PY_VERSION_HEX >= 0x03090000
#define Py_BUILD_CORE
#if defined __GNUC__ && defined HAVE_STD_ATOMIC
#undef HAVE_STD_ATOMIC
#endif
#include <internal/pycore_interp.h>
#endif

#include <functional>

#include <echion/state.h>
#include <echion/vm.h>


class InterpreterInfo
{
public:
    int64_t id = 0;
    void* tstate_head = NULL;
    void* next = NULL;
};

static void for_each_interp(std::function<void(InterpreterInfo& interp)> callback)
{
    InterpreterInfo interpreter_info = {0};

    for (char* interp_addr = (char*)runtime->interpreters.head; interp_addr != NULL;
         interp_addr = (char*)interpreter_info.next)
    {
        if (copy_type(interp_addr + offsetof(PyInterpreterState, id), interpreter_info.id))
            continue;
#if PY_VERSION_HEX >= 0x030b0000
        if (copy_type(interp_addr + offsetof(PyInterpreterState, threads.head),
#else
        if (copy_type(interp_addr + offsetof(PyInterpreterState, tstate_head),
#endif
                      interpreter_info.tstate_head))
            continue;
        if (copy_type(interp_addr + offsetof(PyInterpreterState, next), interpreter_info.next))
            continue;

        callback(interpreter_info);
    };
}
