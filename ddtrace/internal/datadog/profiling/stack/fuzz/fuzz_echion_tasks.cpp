// Fuzz harness: GenInfo::create + TaskInfo::create
//
// Exercises the asyncio task/coroutine introspection pipeline.
// GenInfo::create recursively walks coroutine await chains via PyGenObject
// and PyAsyncGenASend; TaskInfo::create walks TaskObj headers, resolves
// task names through StringTable, and follows task_fut_waiter chains.

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

    uintptr_t p0 = addr_from_u64(load_u64_le(data, size, 0));
    uintptr_t p1 = addr_from_u64(load_u64_le(data, size, 8));

    // GenInfo::create — reads PyGenObject from remote memory, resolves
    // gi_frame_state, walks cr_await / ag_asend chains recursively.
    (void)GenInfo::create(reinterpret_cast<PyObject*>(p0));

    // TaskInfo::create — reads TaskObj header, resolves task_coro via
    // GenInfo::create, task_name via StringTable, and follows
    // task_fut_waiter chain recursively.
    {
        EchionSampler echion_sampler;
        (void)TaskInfo::create(echion_sampler, reinterpret_cast<TaskObj*>(p1));
    }

    g_data = nullptr;
    g_size = 0;
    return 0;
}
