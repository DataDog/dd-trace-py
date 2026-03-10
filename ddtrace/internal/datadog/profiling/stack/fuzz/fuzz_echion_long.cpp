// Fuzz harness: pylong_to_llong
//
// Exercises PyLong object parsing from remote memory on Python 3.12+.
// pylong_to_llong reads the compact/non-compact representation, validates
// digit counts, and copies variable-length digit arrays via copy_generic.

#include "fuzz_common.h"

#include <echion/long.h>

extern "C" int
LLVMFuzzerTestOneInput(const uint8_t* data, size_t size)
{
#if PY_VERSION_HEX >= 0x030c0000
    g_data = data;
    g_size = size;

    if (size == 0) {
        return 0;
    }

    uintptr_t p0 = addr_from_u64(load_u64_le(data, size, 0));

    // pylong_to_llong — reads PyLongObject header from remote memory,
    // checks compact vs multi-digit representation, copies digit array,
    // and reconstructs the value with shifting.
    (void)pylong_to_llong(reinterpret_cast<PyObject*>(p0));

    g_data = nullptr;
    g_size = 0;
#else
    (void)data;
    (void)size;
#endif
    return 0;
}
