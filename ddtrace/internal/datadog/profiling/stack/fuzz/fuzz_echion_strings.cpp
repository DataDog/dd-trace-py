// Fuzz harness: StringTable + pybytes
//
// Lightweight target that exercises PyUnicode and PyBytes parsing via
// StringTable::key() and pybytes_to_bytes_and_size().

#include "fuzz_common.h"

#include <echion/echion_sampler.h>
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
    uintptr_t p1 = addr_from_u64(load_u64_le(data, size, 8));

    EchionSampler echion_sampler;

    // StringTable::key — reads PyUnicodeObject from remote memory; exercises
    // string interning, UTF-8 conversion, and size validation.
    (void)echion_sampler.string_table().key(reinterpret_cast<PyObject*>(p0), StringTag::TaskName);

    // pybytes_to_bytes_and_size — reads PyBytesObject from remote memory;
    // exercises ob_size validation and copy_generic for variable-length data.
    {
        Py_ssize_t bytes_size = 0;
        (void)pybytes_to_bytes_and_size(reinterpret_cast<PyObject*>(p1), &bytes_size);
    }

    g_data = nullptr;
    g_size = 0;
    return 0;
}
