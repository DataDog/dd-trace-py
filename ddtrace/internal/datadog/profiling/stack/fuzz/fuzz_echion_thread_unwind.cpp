// Fuzz harness: ThreadInfo::unwind

#include "fuzz_common.h"

#include <echion/echion_sampler.h>
#include <echion/tasks.h>

extern "C" int
LLVMFuzzerTestOneInput(const uint8_t* data, size_t size)
{
    g_data = data;
    g_size = size;

    if (size == 0) {
        return 0;
    }

    auto p0 = addr_from_u64(load_u64_le(data, size, 0));

    EchionSampler echion_sampler;
    auto tid = 123;
    ThreadInfo thread(tid, tid, "name", 1234);
    PyThreadState tstate;
#if PY_VERSION_HEX >= 0x030d0000
    tstate.current_frame = reinterpret_cast<_PyInterpreterFrame*>(p0);
#elif PY_VERSION_HEX >= 0x030b0000
    tstate.cframe = reinterpret_cast<_PyCFrame*>(p0);
#else
    tstate.frame = reinterpret_cast<PyFrameObject*>(p0);
#endif

#if PY_VERSION_HEX >= 0x030b0000
    auto* p_chunk = reinterpret_cast<_PyStackChunk*>(addr_from_u64(load_u64_le(data, size, 8)));
    tstate.datastack_chunk = p_chunk;
#endif

    // Only flexes the "unwind thread stack" path, greenlets
    // and asyncio aren't used here
    thread.unwind(echion_sampler, &tstate);

    g_data = nullptr;
    g_size = 0;
    return 0;
}
