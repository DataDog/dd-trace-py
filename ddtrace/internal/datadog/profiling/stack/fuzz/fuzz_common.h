// Shared boilerplate for echion fuzz harnesses.
//
// Each harness includes this header and defines LLVMFuzzerTestOneInput.
// The header provides:
//   - A fake "remote memory image" backed by the libFuzzer input buffer, plus
//     helper functions to derive pointers and integers from fuzz data
//     (fuzz_memory_image.h, also reused by the unit tests)
//   - The echion_fuzz_copy_memory() callback wired into echion's vm.h
//   - Stubs for symbols from vm.cc that sampler.cpp references
//   - A standalone main() for non-libFuzzer builds

#pragma once

#include <cstddef>
#include <cstdint>
#include <vector>

#include <echion/vm.h>

#include "fuzz_memory_image.h"

extern "C" int
echion_fuzz_copy_memory(proc_ref_t proc_ref, const void* addr, ssize_t len, void* buf)
{
    (void)proc_ref;
    return echion_fuzz_memory_image_read(addr, len, buf);
}

// Stubs for symbols from vm.cc that sampler.cpp references.
// We cannot compile vm.cc with ECHION_FUZZING because it defines copy_memory(),
// which conflicts with the inline fuzz version from vm.h.
// Fuzzing stub: the real implementation enables/disables fast copy
// and returns the previous state. Under normal (non-error) circumstances fast
// copy is enabled, so returning true here accurately reflects that default.
bool
set_fast_copy_enabled(bool)
{
    return true;
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
