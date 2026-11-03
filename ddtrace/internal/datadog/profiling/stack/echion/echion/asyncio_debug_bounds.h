#pragma once

#include <cstdint>

namespace asyncio_debug {

// Overflow-safe check that [offset, offset + length) lies within [0, size).
inline bool
contains_span(uint64_t size, uint64_t offset, uint64_t length)
{
    return offset <= size && length <= size - offset;
}

} // namespace asyncio_debug
