#include "echion/echion_sampler.h"
#include "echion/task_name.h"
#include "sampler.hpp"

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

    thread.unwind(echion, &empty_tstate, 0);

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

    EXPECT_TRUE(echion.update_code_object_generations({ interpreter(2, 1), interpreter(1, 1) }, true));
    EXPECT_TRUE(echion.frame_cache().lookup(key));

    EXPECT_TRUE(echion.update_code_object_generations({ interpreter(1, 1), interpreter(2, 2) }, true));
    EXPECT_FALSE(echion.frame_cache().lookup(key));

    echion.frame_cache().store(key, std::make_unique<Frame>(10));
    EXPECT_TRUE(echion.update_code_object_generations({ interpreter(1, 1), interpreter(3, 2) }, true));
    EXPECT_FALSE(echion.frame_cache().lookup(key));

    echion.frame_cache().store(key, std::make_unique<Frame>(10));
    EXPECT_FALSE(echion.update_code_object_generations({ interpreter(1, 1), interpreter(3, 2) }, false));
    EXPECT_FALSE(echion.frame_cache().lookup(key));
}
#endif

TEST(SamplingCycleState, GreenletSwitchPreservesLinkedParentFrame)
{
    constexpr GreenletInfo::ID child_id = 101;
    constexpr GreenletInfo::ID parent_id = 102;
    PyObject child_running_frame{};
    PyObject child_suspended_frame{};
    PyObject parent_suspended_frame{};
    PyObject parent_resumed_frame{};

    Datadog::Sampler& sampler = Datadog::Sampler::get();
    EchionSampler& echion = sampler.get_echion();
    {
        std::lock_guard<std::mutex> guard(echion.greenlet_info_map_lock());
        auto& greenlets = echion.greenlet_info_map();
        greenlets.emplace(
          child_id, std::make_unique<GreenletInfo>(child_id, &child_running_frame, TaskName::from_literal("child")));
        greenlets.emplace(
          parent_id,
          std::make_unique<GreenletInfo>(parent_id, &parent_suspended_frame, TaskName::from_literal("parent")));
    }
    sampler.link_greenlets(parent_id, child_id);

    sampler.record_greenlet_switch(child_id, &child_suspended_frame, parent_id, &parent_resumed_frame, false);
    {
        std::lock_guard<std::mutex> guard(echion.greenlet_info_map_lock());
        EXPECT_EQ(echion.greenlet_parent_map().at(child_id), parent_id);
        EXPECT_EQ(echion.greenlet_info_map().at(child_id)->frame, &child_suspended_frame);
        EXPECT_EQ(echion.greenlet_info_map().at(parent_id)->frame, &parent_suspended_frame);
    }

    sampler.record_greenlet_switch(child_id, &child_running_frame, parent_id, &parent_resumed_frame, true);
    {
        std::lock_guard<std::mutex> guard(echion.greenlet_info_map_lock());
        EXPECT_EQ(echion.greenlet_info_map().at(child_id)->frame, &child_running_frame);
        EXPECT_EQ(echion.greenlet_info_map().at(parent_id)->frame, &parent_resumed_frame);
        echion.greenlet_info_map().erase(child_id);
        echion.greenlet_info_map().erase(parent_id);
        echion.greenlet_parent_map().erase(child_id);
    }
}
