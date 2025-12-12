// This file is part of "echion" which is released under MIT.
//
// Copyright (c) 2023 Gabriele N. Tornetta <phoenix1987@gmail.com>.

#pragma once

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#if PY_VERSION_HEX >= 0x030c0000
// https://github.com/python/cpython/issues/108216#issuecomment-1696565797
#undef _PyGC_FINALIZED
#endif

#if defined __GNUC__ && defined HAVE_STD_ATOMIC
#undef HAVE_STD_ATOMIC
#endif
#define Py_BUILD_CORE
#include <internal/pycore_pystate.h>
#if PY_VERSION_HEX >= 0x030e0000
#include <internal/pycore_runtime.h>
#endif

#include <thread>

inline _PyRuntimeState* runtime = &_PyRuntime;
inline PyThreadState* current_tstate = NULL;

inline std::thread* sampler_thread = nullptr;

inline int running = 0;

inline PyObject* asyncio_current_tasks = NULL;
inline PyObject* asyncio_scheduled_tasks = NULL; // WeakSet
inline PyObject* asyncio_eager_tasks = NULL;     // set
