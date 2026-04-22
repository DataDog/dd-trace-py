// Fuzz harness: for_each_interp
//
// Exercises the interpreter linked-list traversal in interp.cc.
// for_each_interp walks _PyRuntimeState.interpreters.head, reading
// PyInterpreterState fields (id, next, tstate_head) via copy_type.
// Tests cycle detection, iteration bounds, and partial-read resilience.

#include "fuzz_common.h"

#include <echion/interp.h>

extern "C" int
LLVMFuzzerTestOneInput(const uint8_t* data, size_t size)
{
    if (size < sizeof(uint64_t)) {
        return 0;
    }

    g_data = data;
    g_size = size;

    // Point interpreters.head into the fuzz buffer so for_each_interp
    // reads interpreter nodes from fuzzer-controlled memory.
    _PyRuntimeState runtime;
    std::memset(&runtime, 0, sizeof(runtime));
    uintptr_t p0 = addr_from_u64(load_u64_le(data, size, 0));
    runtime.interpreters.head = reinterpret_cast<PyInterpreterState*>(p0);

    size_t interp_count = 0;
    for_each_interp(&runtime, [&interp_count](InterpreterInfo&) { interp_count++; });

    g_data = nullptr;
    g_size = 0;
    return 0;
}
