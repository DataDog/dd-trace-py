// Shared boilerplate for echion fuzz harnesses.
//
// Each harness includes this header and defines LLVMFuzzerTestOneInput.
// The header provides:
//   - A fake "remote memory image" backed by the libFuzzer input buffer
//   - Helper functions to derive pointers and integers from fuzz data
//   - The echion_fuzz_copy_memory() callback wired into echion's vm.h
//   - Stubs for symbols from vm.cc that sampler.cpp references
//   - A standalone main() for non-libFuzzer builds

#pragma once

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <vector>

#include <echion/vm.h>

namespace {

static constexpr uintptr_t kRemoteBase = 0x10000000ULL;

static thread_local const uint8_t* g_data = nullptr;
static thread_local size_t g_size = 0;

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

} // namespace

extern "C" int
echion_fuzz_copy_memory(proc_ref_t proc_ref, const void* addr, ssize_t len, void* buf)
{
    (void)proc_ref;

    // Return 0 on success, non-zero on failure (matches copy_memory contract).
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

// Stubs for symbols from vm.cc that sampler.cpp references.
// We cannot compile vm.cc with ECHION_FUZZING because it defines copy_memory(),
// which conflicts with the inline fuzz version from vm.h.
bool
use_alternative_copy_memory()
{
    return false;
}

void
_set_pid(pid_t _pid)
{
    pid = _pid;
}

#ifndef FUZZING_BUILD_MODE_UNSAFE_FOR_PRODUCTION
// Standalone entrypoint for quick sanity runs without linking libFuzzer.
// When building with libFuzzer, the fuzzer runtime provides `main()`.
#include <fstream>
#include <iostream>

extern "C" int
LLVMFuzzerTestOneInput(const uint8_t* data, size_t size);

int
main(int argc, char** argv)
{
    if (argc != 2) {
        std::cerr << "Usage: " << argv[0] << " <input_file>\n";
        return 2;
    }

    std::ifstream f(argv[1], std::ios::binary);
    if (!f) {
        std::cerr << "Failed to open input file\n";
        return 2;
    }

    std::vector<uint8_t> data((std::istreambuf_iterator<char>(f)), std::istreambuf_iterator<char>());
    (void)LLVMFuzzerTestOneInput(data.data(), data.size());
    return 0;
}
#endif
