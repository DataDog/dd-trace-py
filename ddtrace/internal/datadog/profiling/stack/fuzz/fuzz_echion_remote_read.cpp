// Fuzz harness (raw): treat libFuzzer input bytes as a remote memory image and
// let echion attempt to interpret *any* garbage.
//
// This is intentionally a "minimal structure" harness: we do not synthesize
// valid CPython object layouts. Instead, we pass fuzz-derived remote addresses
// into echion APIs and rely on echion's own size caps (e.g. MAX_MIRROR_SIZE,
// MAX_STRING_SIZE) to keep the harness stable.

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <vector>

#include <echion/echion_sampler.h>
#include <echion/frame.h>
#include <echion/mirrors.h>
#include <echion/strings.h>
#include <echion/vm.h>

#if PY_VERSION_HEX >= 0x030b0000
#include <echion/stack_chunk.h>
#endif

#if PY_VERSION_HEX >= 0x030a0000
// Expose PyCodeObject fields for local stack allocation and field assignment.
// Echion itself uses internal headers; the fuzz harness does the same.
#ifndef Py_BUILD_CORE
#define Py_BUILD_CORE
#endif
#include <internal/pycore_code.h>
#endif

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

extern "C" int
LLVMFuzzerTestOneInput(const uint8_t* data, size_t size)
{
    g_data = data;
    g_size = size;

    if (size == 0) {
        return 0;
    }

    // Pick fuzz generated values.
    // pointers: "remote address" inside the input data
    // p0-p1 are used for StackChunk, p2-p4 for PyCodeObject fields.
    uintptr_t p0 = addr_from_u64(load_u64_le(data, size, 0));
    uintptr_t p1 = addr_from_u64(load_u64_le(data, size, 8));
    uintptr_t p2 = addr_from_u64(load_u64_le(data, size, 16));
    uintptr_t p3 = addr_from_u64(load_u64_le(data, size, 24));
    uintptr_t p4 = addr_from_u64(load_u64_le(data, size, 32));

    // lasti: last instruction index, used by Frame::create()
    int lasti = load_int_le(data, size, 40);

#if PY_VERSION_HEX >= 0x030b0000
    {
        StackChunk sc;
        (void)sc.update(reinterpret_cast<_PyStackChunk*>(p0));
        (void)sc.resolve(reinterpret_cast<void*>(p1));
    }
#endif

    {
        // Create a *local* PyCodeObject with pointers to arbitrary remote garbage.
        // Frame::create() will attempt to read those remote objects via copy_type/copy_generic.
        PyCodeObject code{};
        code.co_firstlineno = 1;
        code.co_filename = reinterpret_cast<PyObject*>(p2);

#if PY_VERSION_HEX >= 0x030b0000
        code.co_qualname = reinterpret_cast<PyObject*>(p3);
        code.co_linetable = reinterpret_cast<PyObject*>(p4);
#elif PY_VERSION_HEX >= 0x030a0000
        code.co_name = reinterpret_cast<PyObject*>(p3);
        code.co_linetable = reinterpret_cast<PyObject*>(p4);
#else
        code.co_name = reinterpret_cast<PyObject*>(p3);
        code.co_lnotab = reinterpret_cast<PyObject*>(p4);
#endif

        // CORE of the fuzz harness
        EchionSampler echion_sampler;
        (void)Frame::create(echion_sampler, &code, lasti);
        // TODO: Call more internal functions to trigger more code paths
        // Possible ideas:
        // - MirrorSet::*
        // - StackChunk::*
        // - ThreadInfo::*
    }

    g_data = nullptr;
    g_size = 0;
    return 0;
}

#ifndef FUZZING_BUILD_MODE_UNSAFE_FOR_PRODUCTION
// Standalone entrypoint for quick sanity runs without linking libFuzzer.
// When building with libFuzzer, the fuzzer runtime provides `main()`.
#include <fstream>
#include <iostream>

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
