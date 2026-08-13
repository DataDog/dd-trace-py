#include "echion/echion_sampler.h"
#include "echion/task_name.h"

#include <gtest/gtest.h>

#include <memory>

#if PY_VERSION_HEX >= 0x030e0000
namespace {
InterpreterInfo
interpreter(int64_t id, uint64_t generation)
{
    InterpreterInfo info;
    info.id = id;
    info.code_object_generation = generation;
    return info;
}
} // namespace
#endif

TEST(SamplingCycleState, UnwindReplacesTaskAndGreenletStacksFromPriorCycle)
{
    EchionSampler echion;
#if defined PL_LINUX
    ThreadInfo thread(1, 1, "test-thread", CLOCK_THREAD_CPUTIME_ID);
#elif defined PL_DARWIN
    ThreadInfo thread(1, 1, "test-thread", mach_thread_self());
#endif
    PyThreadState empty_tstate{};

    thread.current_tasks.push_back(std::make_unique<StackInfo>(TaskName::from_literal("stale-task"), false, 1));
    thread.current_greenlets.push_back(std::make_unique<StackInfo>(TaskName::from_literal("stale-greenlet"), false, 2));

    thread.unwind(echion, &empty_tstate);

    EXPECT_TRUE(thread.current_tasks.empty());
    EXPECT_TRUE(thread.current_greenlets.empty());
}

#if PY_VERSION_HEX >= 0x030e0000
TEST(SamplingCycleState, CodeObjectGenerationInvalidatesFrameIdentityCache)
{
    EchionSampler echion(2);
    constexpr Frame::Key key = 42;

    ASSERT_TRUE(echion.update_code_object_generations({ interpreter(1, 1), interpreter(2, 1) }, true));
    echion.frame_cache().store(key, std::make_unique<Frame>(10));
    echion.asyncio_frame_cache_key() = key;
    echion.uvloop_frame_cache_key() = key;

    EXPECT_TRUE(echion.update_code_object_generations({ interpreter(2, 1), interpreter(1, 1) }, true));
    EXPECT_TRUE(echion.frame_cache().lookup(key));

    EXPECT_TRUE(echion.update_code_object_generations({ interpreter(1, 1), interpreter(2, 2) }, true));
    EXPECT_FALSE(echion.frame_cache().lookup(key));
    EXPECT_FALSE(echion.asyncio_frame_cache_key());
    EXPECT_FALSE(echion.uvloop_frame_cache_key());

    echion.frame_cache().store(key, std::make_unique<Frame>(10));
    EXPECT_TRUE(echion.update_code_object_generations({ interpreter(1, 1), interpreter(3, 2) }, true));
    EXPECT_FALSE(echion.frame_cache().lookup(key));

    echion.frame_cache().store(key, std::make_unique<Frame>(10));
    EXPECT_FALSE(echion.update_code_object_generations({ interpreter(1, 1), interpreter(3, 2) }, false));
    EXPECT_FALSE(echion.frame_cache().lookup(key));
}
#endif
