#include "cpu_timer_tid_table.hpp"

#include <gtest/gtest.h>

#include <atomic>
#include <cstddef>
#include <cstdint>
#include <thread>
#include <vector>

using Datadog::CpuTimer::CpuTimerTidTable;

namespace {

struct TestState
{
    uint64_t value;
};

} // namespace

TEST(CpuTimerTidTable, InitializesOnlyDirectory)
{
    CpuTimerTidTable<TestState, 4> table;

    ASSERT_TRUE(table.initialize(16));
    EXPECT_EQ(table.max_tid(), 16u);
    EXPECT_EQ(table.directory_size(), 4u);
    EXPECT_EQ(table.allocated_page_count(), 0u);
    EXPECT_FALSE(table.contains(0));
    EXPECT_TRUE(table.contains(1));
    EXPECT_TRUE(table.contains(16));
    EXPECT_FALSE(table.contains(17));
    EXPECT_EQ(table.load(1), nullptr);
}

TEST(CpuTimerTidTable, AllocatesLeavesLazily)
{
    CpuTimerTidTable<TestState, 4> table;
    TestState first{ 1 };
    TestState same_page{ 2 };
    TestState next_page{ 3 };

    ASSERT_TRUE(table.initialize(16));
    ASSERT_TRUE(table.ensure(1));
    EXPECT_EQ(table.allocated_page_count(), 1u);
    ASSERT_TRUE(table.ensure(3));
    EXPECT_EQ(table.allocated_page_count(), 1u);
    ASSERT_TRUE(table.ensure(5));
    EXPECT_EQ(table.allocated_page_count(), 2u);

    ASSERT_TRUE(table.publish(1, &first));
    ASSERT_TRUE(table.publish(3, &same_page));
    ASSERT_TRUE(table.publish(5, &next_page));
    EXPECT_EQ(table.load(1), &first);
    EXPECT_EQ(table.load(3), &same_page);
    EXPECT_EQ(table.load(5), &next_page);
    EXPECT_EQ(table.load(8), nullptr);
}

TEST(CpuTimerTidTable, TracksHandlerActivityPerTid)
{
    CpuTimerTidTable<TestState, 4> table;
    TestState state{ 1 };
    TestState* observed = nullptr;
    CpuTimerTidTable<TestState, 4>::HandlerToken token;

    ASSERT_TRUE(table.initialize(16));
    ASSERT_TRUE(table.ensure(5));
    ASSERT_TRUE(table.publish(5, &state));
    EXPECT_FALSE(table.is_handler_active(5));
    ASSERT_TRUE(table.enter_handler(5, observed, token));
    EXPECT_EQ(observed, &state);
    EXPECT_TRUE(table.is_handler_active(5));
    EXPECT_FALSE(table.is_handler_active(6));
    table.leave_handler(token);
    EXPECT_FALSE(table.is_handler_active(5));

    EXPECT_FALSE(table.enter_handler(9, observed, token));
    EXPECT_FALSE(table.is_handler_active(9));
}

TEST(CpuTimerTidTable, ClearAndResetRetainAllocatedLeaves)
{
    CpuTimerTidTable<TestState, 4> table;
    TestState first{ 1 };
    TestState second{ 2 };

    ASSERT_TRUE(table.initialize(16));
    ASSERT_TRUE(table.ensure(1));
    ASSERT_TRUE(table.ensure(8));
    ASSERT_TRUE(table.publish(1, &first));
    ASSERT_TRUE(table.publish(8, &second));
    TestState* observed = nullptr;
    CpuTimerTidTable<TestState, 4>::HandlerToken first_token;
    CpuTimerTidTable<TestState, 4>::HandlerToken second_token;
    ASSERT_TRUE(table.enter_handler(1, observed, first_token));
    ASSERT_TRUE(table.enter_handler(8, observed, second_token));

    table.clear(1);
    EXPECT_EQ(table.load(1), nullptr);
    EXPECT_EQ(table.load(8), &second);
    EXPECT_TRUE(table.is_handler_active(1));

    table.reset();
    EXPECT_EQ(table.load(1), nullptr);
    EXPECT_EQ(table.load(8), nullptr);
    EXPECT_FALSE(table.is_handler_active(1));
    EXPECT_FALSE(table.is_handler_active(8));
    EXPECT_EQ(table.allocated_page_count(), 2u);
}

TEST(CpuTimerTidTable, ConcurrentEnsurePublishesOneLeaf)
{
    constexpr size_t thread_count = 16;
    CpuTimerTidTable<TestState, 64> table;
    ASSERT_TRUE(table.initialize(1024));

    std::atomic<bool> start{ false };
    std::vector<std::thread> threads;
    threads.reserve(thread_count);
    for (size_t i = 0; i < thread_count; i++) {
        threads.emplace_back([&] {
            while (!start.load(std::memory_order_acquire)) {
                std::this_thread::yield();
            }
            EXPECT_TRUE(table.ensure(127));
        });
    }

    start.store(true, std::memory_order_release);
    for (auto& thread : threads) {
        thread.join();
    }

    EXPECT_EQ(table.allocated_page_count(), 1u);
}
