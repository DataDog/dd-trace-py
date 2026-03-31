// Fuzz harness: TaskInfo::unwind
//
// Exercises the task coroutine chain unwinding. TaskInfo::create
// builds a TaskInfo with a GenInfo coroutine chain, then unwind()
// walks the coro→await chain, pushing frames onto a FrameStack.
// This tests recursive GenInfo resolution, coroutine frame extraction,
// and the uvloop wrapper frame detection.

#include "fuzz_common.h"

#include <echion/echion_sampler.h>
#include <echion/tasks.h>

extern "C" int
LLVMFuzzerTestOneInput(const uint8_t* data, size_t size)
{
    if (size < 2 * sizeof(uint64_t)) {
        return 0;
    }

    g_data = data;
    g_size = size;

    uintptr_t p0 = addr_from_u64(load_u64_le(data, size, 0));
    uint8_t flags = (size > 16) ? data[16] : 0;
    bool using_uvloop = (flags & 1) != 0;

    EchionSampler echion_sampler;

    // Create a TaskInfo from fuzz-controlled memory, then exercise
    // the unwind path which walks the coroutine chain and builds
    // a FrameStack.
    auto maybe_task = TaskInfo::create(echion_sampler, reinterpret_cast<TaskObj*>(p0));
    if (maybe_task) {
        FrameStack stack;
        (void)(*maybe_task)->unwind(echion_sampler, stack, using_uvloop);
    }

    g_data = nullptr;
    g_size = 0;
    return 0;
}
