// Fuzz harness: Frame::read + Frame::get
//
// Exercises the frame reading pipeline directly: Frame::read parses
// _PyInterpreterFrame (3.11+) or PyFrameObject, validates owner bits,
// computes lasti from instruction pointers, and resolves the frame
// through Frame::get which includes LRU cache lookup/store and the
// ABA-problem mitigation via co_firstlineno in the cache key.

#include "fuzz_common.h"

#include <echion/echion_sampler.h>
#include <echion/frame.h>

extern "C" int
LLVMFuzzerTestOneInput(const uint8_t* data, size_t size)
{
    if (size < 3 * sizeof(uint64_t)) {
        return 0;
    }

    g_data = data;
    g_size = size;

    uintptr_t p0 = addr_from_u64(load_u64_le(data, size, 0));
    uintptr_t p1 = addr_from_u64(load_u64_le(data, size, 8));
    int lasti = load_int_le(data, size, 16);

    EchionSampler echion_sampler;

    // Frame::read — reads a frame from remote memory, validates owner
    // fields (FRAME_OWNED_BY_CSTACK, etc.), computes lasti from
    // instruction pointers, and resolves the code object.
    {
#if PY_VERSION_HEX >= 0x030b0000
        _PyInterpreterFrame* prev_addr = nullptr;
        (void)Frame::read(echion_sampler, reinterpret_cast<_PyInterpreterFrame*>(p0), &prev_addr);
#else
        PyObject* prev_addr = nullptr;
        (void)Frame::read(echion_sampler, reinterpret_cast<PyObject*>(p0), &prev_addr);
#endif
    }

    // Frame::get — reads co_firstlineno for ABA detection, performs
    // LRU cache lookup, and on miss reads the full PyCodeObject
    // to create a new Frame via Frame::create.
    {
        (void)Frame::get(echion_sampler, reinterpret_cast<PyCodeObject*>(p1), lasti);
    }

    g_data = nullptr;
    g_size = 0;
    return 0;
}
