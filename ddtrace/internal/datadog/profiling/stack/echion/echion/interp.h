// This file is part of "echion" which is released under MIT.
//
// Copyright (c) 2023 Gabriele N. Tornetta <phoenix1987@gmail.com>.

#pragma once

#define PY_SSIZE_T_CLEAN

#define Py_BUILD_CORE
#include <Python.h>

#if PY_VERSION_HEX >= 0x03090000
#if defined __GNUC__ && defined HAVE_STD_ATOMIC
#undef HAVE_STD_ATOMIC
#endif
#include <internal/pycore_interp.h>
#endif

#include <cstddef>
#include <cstdint>
#include <functional>

#include <echion/state.h>
#include <echion/vm.h>

class InterpreterInfo
{
  public:
    PyInterpreterState* interp = nullptr;
    int64_t id = 0;
    void* tstate_head = NULL;
    void* next = NULL;
#if PY_VERSION_HEX >= 0x030e0000
    uint64_t code_object_generation = 0;
#endif
};

constexpr size_t MAX_INTERPRETERS = 256;

enum class InterpreterTraversalIssue : uint8_t
{
    CodeObjectGenerationUnreadable = 1 << 0,
    NextUnreadable = 1 << 1,
    IdUnreadable = 1 << 2,
    ThreadHeadUnreadable = 1 << 3,
    CycleDetected = 1 << 4,
    LimitExceeded = 1 << 5,
    EmptyInventory = 1 << 6,
};

class InterpreterTraversalResult
{
    uint8_t issues_ = 0;

  public:
    void add(InterpreterTraversalIssue issue) { issues_ |= static_cast<uint8_t>(issue); }
    [[nodiscard]] bool has(InterpreterTraversalIssue issue) const
    {
        return (issues_ & static_cast<uint8_t>(issue)) != 0;
    }
    [[nodiscard]] bool complete() const { return issues_ == 0; }
};

[[nodiscard]] InterpreterTraversalResult
for_each_interp(_PyRuntimeState* runtime, const std::function<void(InterpreterInfo& interp)>& callback);
