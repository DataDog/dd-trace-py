// Fuzz harness: MirrorSet
//
// Exercises PySetObject header reading, size bounds checking, and table
// iteration via MirrorSet::create() and as_unordered_set().

#include "fuzz_common.h"

#include <echion/mirrors.h>

extern "C" int
LLVMFuzzerTestOneInput(const uint8_t* data, size_t size)
{
    g_data = data;
    g_size = size;

    if (size == 0) {
        return 0;
    }

    uintptr_t p0 = addr_from_u64(load_u64_le(data, size, 0));

    // MirrorSet::create — reads PySetObject header + entry table via
    // copy_generic; exercises size bounds checking and table iteration.
    auto maybe_mirror = MirrorSet::create(reinterpret_cast<PyObject*>(p0));
    if (maybe_mirror) {
        (void)maybe_mirror->as_unordered_set();
    }

    g_data = nullptr;
    g_size = 0;
    return 0;
}
