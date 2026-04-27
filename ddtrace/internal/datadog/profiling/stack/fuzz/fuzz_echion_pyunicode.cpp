// Fuzz harness: pyunicode_to_utf8
//
// Exercises PyUnicodeObject parsing from remote memory.
// pyunicode_to_utf8 reads the PyASCIIObject header, validates
// string kind and compact state, resolves data pointer, copies
// the character buffer via copy_generic, and returns a std::string.

#include "fuzz_common.h"

#include <echion/strings.h>

extern "C" int
LLVMFuzzerTestOneInput(const uint8_t* data, size_t size)
{
    g_data = data;
    g_size = size;

    if (size == 0) {
        return 0;
    }

    uintptr_t p0 = addr_from_u64(load_u64_le(data, size, 0));

    // pyunicode_to_utf8 — reads PyUnicodeObject from remote memory,
    // validates string kind (must be 1 for Latin-1/ASCII), checks
    // compact vs non-compact layout, resolves data pointer, and
    // copies the string content via copy_generic.
    (void)pyunicode_to_utf8(reinterpret_cast<PyObject*>(p0));

    g_data = nullptr;
    g_size = 0;
    return 0;
}
