// A fake "remote process image" for echion's copy_memory, backed by a caller-supplied byte buffer.
//
// Split out of fuzz_common.h so unit tests can reuse the reader without also pulling in the vm.cc
// stubs and the standalone main() that only the fuzz harnesses need. Includers must be built with
// ECHION_FUZZING and must themselves define the extern "C" echion_fuzz_copy_memory() hook declared
// by echion/vm.h, delegating to echion_fuzz_memory_image_read().

#pragma once

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstring>

#include <echion/vm.h>

namespace {

static constexpr uintptr_t kRemoteBase = 0x10000000ULL;

static thread_local const uint8_t* g_data = nullptr;
static thread_local size_t g_size = 0;

// Point the fake image at `data`; pass (nullptr, 0) to detach it again.
static inline void
set_memory_image(const uint8_t* data, size_t size)
{
    g_data = data;
    g_size = size;
}

static inline uintptr_t
addr_from_u64(uint64_t v)
{
    if (g_size == 0) {
        return kRemoteBase;
    }
    return kRemoteBase + static_cast<uintptr_t>(v % g_size);
}

static inline uint64_t
load_u64_le(const uint8_t* data, size_t size, size_t off)
{
    uint64_t v = 0;
    if (off >= size) {
        return 0;
    }
    const size_t n = std::min<size_t>(8, size - off);
    std::memcpy(&v, data + off, n);
    return v;
}

static inline int
load_int_le(const uint8_t* data, size_t size, size_t off)
{
    int v = 0;
    if (off >= size) {
        return 0;
    }
    const size_t n = std::min<size_t>(4, size - off);
    std::memcpy(&v, data + off, n);
    return v;
}

// Returns 0 on success, non-zero on failure (matches the copy_memory contract).
static inline int
echion_fuzz_memory_image_read(const void* addr, ssize_t len, void* buf)
{
    if (!g_data || !buf || len < 0) {
        return -1;
    }

    // Keep individual reads bounded to avoid pathological slow paths.
    static constexpr size_t kMaxCopy = 2U << 20; // 2 MiB
    if (static_cast<size_t>(len) > kMaxCopy) {
        return -1;
    }

    uintptr_t a = reinterpret_cast<uintptr_t>(addr);
    if (a >= kRemoteBase) {
        size_t off = static_cast<size_t>(a - kRemoteBase);
        if (off + static_cast<size_t>(len) <= g_size) {
            std::memcpy(buf, g_data + off, static_cast<size_t>(len));
            return 0;
        }
    }

    return -1;
}

} // namespace
