// This file is part of "echion" which is released under MIT.
//
// Copyright (c) 2023 Gabriele N. Tornetta <phoenix1987@gmail.com>.

#pragma once

#define PY_SSIZE_T_CLEAN
#define Py_BUILD_CORE
#include <Python.h>

#if PY_VERSION_HEX >= 0x030b0000

#if defined __GNUC__ && defined HAVE_STD_ATOMIC
#undef HAVE_STD_ATOMIC
#endif
#include <internal/pycore_frame.h>
#include <internal/pycore_pystate.h>

#include <memory>
#include <vector>

#include <echion/errors.h>
#include <echion/vm.h>

const constexpr size_t MAX_CHUNK_SIZE = 256 * 1024; // 256KB

// ----------------------------------------------------------------------------
class StackChunk
{
  public:
    StackChunk() {}

    [[nodiscard]] Result<void> update(_PyStackChunk* chunk_addr);
    void* resolve(void* frame_addr);
    bool is_valid() const;

  private:
    void* origin = NULL;
    std::vector<char> data;
    size_t data_capacity = 0;
    std::unique_ptr<StackChunk> previous = nullptr;
};

#endif // PY_VERSION_HEX >= 0x030b0000