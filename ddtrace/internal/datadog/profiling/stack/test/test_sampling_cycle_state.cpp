#include "echion/echion_sampler.h"
#include "echion/task_name.h"

#include <gtest/gtest.h>

#include <memory>

#if defined PL_LINUX
TEST(ThreadInfoCreate, IgnoresNonPthreadPythonThreadId)
{
    // Linux limits TIDs to less than 2^22, so this exercises clock_gettime(EINVAL).
    constexpr unsigned long invalid_native_id = 1UL << 24;
    auto thread = ThreadInfo::create(1, invalid_native_id, "test-thread");

    ASSERT_TRUE(thread);
    EXPECT_EQ((*thread)->thread_id, 1);
    EXPECT_EQ((*thread)->cpu_time, 0);
}
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
