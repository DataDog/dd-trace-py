#include "cpu_timer_tid_table.hpp"

#include <gtest/gtest.h>

#include <atomic>
#include <cstddef>
#include <cstdint>
#include <thread>
#include <vector>

using Datadog::CpuTimer::CpuTimerTidTable;
using Datadog::CpuTimer::kCpuTimerTidTablePageSize;

namespace {

struct TestState
{
    uint64_t value;
};

} // namespace

TEST(CpuTimerTidTable, InitializesOnlyDirectory)
{
    CpuTimerTidTable<TestState> table;
    constexpr size_t max_tid = 4 * kCpuTimerTidTablePageSize;

    ASSERT_TRUE(table.initialize(max_tid));
    EXPECT_EQ(table.max_tid(), max_tid);
    EXPECT_EQ(table.directory_size(), 4u);
    EXPECT_EQ(table.allocated_page_count(), 0u);
    EXPECT_FALSE(table.contains(0));
    EXPECT_TRUE(table.contains(1));
    EXPECT_TRUE(table.contains(max_tid));
    EXPECT_FALSE(table.contains(max_tid + 1));
    EXPECT_EQ(table.load(1), nullptr);
}

TEST(CpuTimerTidTable, AllocatesLeavesLazily)
{
    CpuTimerTidTable<TestState> table;
    TestState first{ 1 };
    TestState same_page{ 2 };
    TestState next_page{ 3 };
    constexpr size_t same_page_tid = kCpuTimerTidTablePageSize;
    constexpr size_t next_page_tid = kCpuTimerTidTablePageSize + 1;

    ASSERT_TRUE(table.initialize(4 * kCpuTimerTidTablePageSize));
    ASSERT_TRUE(table.ensure(1));
    EXPECT_EQ(table.allocated_page_count(), 1u);
    ASSERT_TRUE(table.ensure(same_page_tid));
    EXPECT_EQ(table.allocated_page_count(), 1u);
    ASSERT_TRUE(table.ensure(next_page_tid));
    EXPECT_EQ(table.allocated_page_count(), 2u);

    ASSERT_TRUE(table.publish(1, &first));
    ASSERT_TRUE(table.publish(same_page_tid, &same_page));
    ASSERT_TRUE(table.publish(next_page_tid, &next_page));
    EXPECT_EQ(table.load(1), &first);
    EXPECT_EQ(table.load(same_page_tid), &same_page);
    EXPECT_EQ(table.load(next_page_tid), &next_page);
    EXPECT_EQ(table.load(2 * kCpuTimerTidTablePageSize), nullptr);
}

TEST(CpuTimerTidTable, PublishesCurrentGreenletIdentity)
{
    CpuTimerTidTable<TestState> table;

    ASSERT_TRUE(table.initialize(16));
    ASSERT_TRUE(table.ensure(5));
    EXPECT_EQ(table.current_greenlet_id(5), 0u);

    table.set_current_greenlet_id(5, 123);
    EXPECT_EQ(table.current_greenlet_id(5), 123u);
    EXPECT_EQ(table.current_greenlet_id(6), 0u);

    table.clear(5);
    EXPECT_EQ(table.current_greenlet_id(5), 0u);
}

TEST(CpuTimerTidTable, TracksHandlerActivityPerTid)
{
    CpuTimerTidTable<TestState> table;
    TestState state{ 1 };
    TestState* observed = nullptr;
    CpuTimerTidTable<TestState>::HandlerToken token;
    constexpr size_t unallocated_page_tid = kCpuTimerTidTablePageSize + 1;

    ASSERT_TRUE(table.initialize(2 * kCpuTimerTidTablePageSize));
    ASSERT_TRUE(table.ensure(5));
    ASSERT_TRUE(table.publish(5, &state));
    EXPECT_FALSE(table.is_handler_active(5));
    ASSERT_TRUE(table.enter_handler(5, observed, token));
    EXPECT_EQ(observed, &state);
    EXPECT_TRUE(table.is_handler_active(5));
    EXPECT_FALSE(table.is_handler_active(6));
    table.leave_handler(token);
    EXPECT_FALSE(table.is_handler_active(5));

    EXPECT_FALSE(table.enter_handler(unallocated_page_tid, observed, token));
    EXPECT_FALSE(table.is_handler_active(unallocated_page_tid));
}

TEST(CpuTimerTidTable, ClearAndResetRetainAllocatedLeaves)
{
    CpuTimerTidTable<TestState> table;
    TestState first{ 1 };
    TestState second{ 2 };
    constexpr size_t second_page_tid = kCpuTimerTidTablePageSize + 1;

    ASSERT_TRUE(table.initialize(2 * kCpuTimerTidTablePageSize));
    ASSERT_TRUE(table.ensure(1));
    ASSERT_TRUE(table.ensure(second_page_tid));
    ASSERT_TRUE(table.publish(1, &first));
    ASSERT_TRUE(table.publish(second_page_tid, &second));
    table.set_current_greenlet_id(1, 101);
    table.set_current_greenlet_id(second_page_tid, 108);
    TestState* observed = nullptr;
    CpuTimerTidTable<TestState>::HandlerToken first_token;
    CpuTimerTidTable<TestState>::HandlerToken second_token;
    ASSERT_TRUE(table.enter_handler(1, observed, first_token));
    ASSERT_TRUE(table.enter_handler(second_page_tid, observed, second_token));

    table.clear(1);
    EXPECT_EQ(table.load(1), nullptr);
    EXPECT_EQ(table.current_greenlet_id(1), 0u);
    EXPECT_EQ(table.load(second_page_tid), &second);
    EXPECT_EQ(table.current_greenlet_id(second_page_tid), 108u);
    EXPECT_TRUE(table.is_handler_active(1));

    table.reset();
    EXPECT_EQ(table.load(1), nullptr);
    EXPECT_EQ(table.load(second_page_tid), nullptr);
    EXPECT_EQ(table.current_greenlet_id(second_page_tid), 0u);
    EXPECT_FALSE(table.is_handler_active(1));
    EXPECT_FALSE(table.is_handler_active(second_page_tid));
    EXPECT_EQ(table.allocated_page_count(), 2u);
}

TEST(CpuTimerTidTable, ConcurrentEnsurePublishesOneLeaf)
{
    constexpr size_t thread_count = 16;
    CpuTimerTidTable<TestState> table;
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
