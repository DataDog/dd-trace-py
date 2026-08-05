#include "echion/echion_sampler.h"

#include <gtest/gtest.h>

#include <memory>

TEST(SamplingCycleState, UnwindReplacesTaskAndGreenletStacksFromPriorCycle)
{
    EchionSampler echion;
#if defined PL_LINUX
    ThreadInfo thread(1, 1, "test-thread", CLOCK_THREAD_CPUTIME_ID);
#elif defined PL_DARWIN
    ThreadInfo thread(1, 1, "test-thread", mach_thread_self());
#endif
    PyThreadState empty_tstate{};

    thread.current_tasks.push_back(std::make_unique<StackInfo>(StringTable::UNKNOWN, false));
    thread.current_greenlets.push_back(std::make_unique<StackInfo>(StringTable::UNKNOWN, false));

    thread.unwind(echion, &empty_tstate);

    EXPECT_TRUE(thread.current_tasks.empty());
    EXPECT_TRUE(thread.current_greenlets.empty());
}
