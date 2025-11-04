// This file is part of "echion" which is released under MIT.
//
// Copyright (c) 2023 Gabriele N. Tornetta <phoenix1987@gmail.com>.

#pragma once

#define PY_SSIZE_T_CLEAN
#include <Python.h>
#if defined __GNUC__ && defined HAVE_STD_ATOMIC
#undef HAVE_STD_ATOMIC
#endif
#define Py_BUILD_CORE
#include <internal/pycore_pystate.h>

#include <memory>
#include <vector>

#include <echion/errors.h>
#include <echion/vm.h>


// ----------------------------------------------------------------------------
class StackChunk
{
public:
    StackChunk() {}

    [[nodiscard]] inline Result<void> update(_PyStackChunk* chunk_addr);
    inline void* resolve(void* frame_addr);
    inline bool is_valid() const;

private:
    void* origin = NULL;
    std::vector<char> data;
    size_t data_capacity = 0;
    std::unique_ptr<StackChunk> previous = nullptr;
};

// ----------------------------------------------------------------------------
Result<void> StackChunk::update(_PyStackChunk* chunk_addr)
{
    _PyStackChunk chunk;

    if (copy_type(chunk_addr, chunk))
    {
        return ErrorKind::StackChunkError;
    }

    origin = chunk_addr;
    // if data_capacity is not enough, reallocate.
    if (chunk.size > data_capacity)
    {
        data_capacity = std::max(chunk.size, data_capacity);
        data.resize(data_capacity);
    }

    // Copy the data up until the size of the chunk
    if (copy_generic(chunk_addr, data.data(), chunk.size))
    {
        return ErrorKind::StackChunkError;
    }

    if (chunk.previous != NULL)
    {
        if (previous == nullptr)
            previous = std::make_unique<StackChunk>();

        auto update_success = previous->update(reinterpret_cast<_PyStackChunk*>(chunk.previous));
        if (!update_success)
        {
            previous = nullptr;
        }
    }

    return Result<void>::ok();
}

// ----------------------------------------------------------------------------
void* StackChunk::resolve(void* address)
{
    // If data is not properly initialized, simply return the address
    if (!is_valid())
    {
        return address;
    }

    _PyStackChunk* chunk = reinterpret_cast<_PyStackChunk*>(data.data());

    // Check if this chunk contains the address
    if (address >= origin && address < reinterpret_cast<char*>(origin) + chunk->size)
        return reinterpret_cast<char*>(chunk) +
               (reinterpret_cast<char*>(address) - reinterpret_cast<char*>(origin));

    if (previous)
        return previous->resolve(address);

    return address;
}

// ----------------------------------------------------------------------------
bool StackChunk::is_valid() const
{
    return data_capacity > 0 && data.size() > 0 && data.size() >= sizeof(_PyStackChunk) &&
           data.data() != nullptr && origin != nullptr;
}

// ----------------------------------------------------------------------------

inline std::unique_ptr<StackChunk> stack_chunk = nullptr;
