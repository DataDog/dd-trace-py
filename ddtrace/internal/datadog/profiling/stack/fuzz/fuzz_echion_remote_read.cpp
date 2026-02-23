// Fuzz harness: StackChunk + Frame::create
//
// Exercises code object parsing and line table decoding via echion's
// Frame::create(), plus StackChunk update/resolve on Python 3.11+.

#include "fuzz_common.h"

#include <echion/echion_sampler.h>
#include <echion/frame.h>

#if PY_VERSION_HEX >= 0x030b0000
#include <echion/stack_chunk.h>
#endif

#if PY_VERSION_HEX >= 0x030a0000
#ifndef Py_BUILD_CORE
#define Py_BUILD_CORE
#endif
#include <internal/pycore_code.h>
#endif

extern "C" int
LLVMFuzzerTestOneInput(const uint8_t* data, size_t size)
{
    g_data = data;
    g_size = size;

    if (size == 0) {
        return 0;
    }

    uintptr_t p0 = addr_from_u64(load_u64_le(data, size, 0));
    uintptr_t p1 = addr_from_u64(load_u64_le(data, size, 8));
    uintptr_t p2 = addr_from_u64(load_u64_le(data, size, 16));
    uintptr_t p3 = addr_from_u64(load_u64_le(data, size, 24));
    uintptr_t p4 = addr_from_u64(load_u64_le(data, size, 32));
    int lasti = load_int_le(data, size, 40);

#if PY_VERSION_HEX >= 0x030b0000
    {
        StackChunk sc;
        (void)sc.update(reinterpret_cast<_PyStackChunk*>(p0));
        (void)sc.resolve(reinterpret_cast<void*>(p1));
    }
#endif

    {
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

        EchionSampler echion_sampler;
        (void)Frame::create(echion_sampler, &code, lasti);
    }

    g_data = nullptr;
    g_size = 0;
    return 0;
}
