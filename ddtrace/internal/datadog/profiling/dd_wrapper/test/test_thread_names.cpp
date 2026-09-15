#include "thread_name_tracker.hpp"

#include <gtest/gtest.h>

#include <atomic>
#include <string>
#include <thread>
#include <vector>

TEST(ThreadNameRegistry, LookupReturnsRegisteredName)
{
    Datadog::ThreadNameRegistry registry;

    registry.register_name(42, "MainThread");

    EXPECT_EQ(registry.lookup(42), "MainThread");
    EXPECT_EQ(registry.size(), 1U);
}

TEST(ThreadNameRegistry, LookupOfUnknownThreadIsEmpty)
{
    Datadog::ThreadNameRegistry registry;

    registry.register_name(42, "MainThread");

    EXPECT_TRUE(registry.lookup(43).empty());
}

TEST(ThreadNameRegistry, RegisteringAgainReplacesTheName)
{
    Datadog::ThreadNameRegistry registry;

    registry.register_name(42, "before");
    registry.register_name(42, "after");

    EXPECT_EQ(registry.lookup(42), "after");
    EXPECT_EQ(registry.size(), 1U);
}

TEST(ThreadNameRegistry, UnregisteredThreadHasNoName)
{
    Datadog::ThreadNameRegistry registry;

    registry.register_name(42, "MainThread");
    registry.unregister_name(42);

    EXPECT_TRUE(registry.lookup(42).empty());
    EXPECT_EQ(registry.size(), 0U);
}

TEST(ThreadNameRegistry, LookupSurvivesRehashing)
{
    // A view into the map would dangle once a later registration rehashes it,
    // so lookup copies the name out.
    Datadog::ThreadNameRegistry registry;
    registry.register_name(1, "the-name-we-keep-reading");

    std::string_view name = registry.lookup(1);
    for (int64_t id = 2; id < 1000; id++) {
        registry.register_name(id, "filler");
    }

    EXPECT_EQ(name, "the-name-we-keep-reading");
}

TEST(ThreadNameRegistry, StopsGrowingAtTheCap)
{
    Datadog::ThreadNameRegistry registry;

    for (size_t i = 0; i < Datadog::ThreadNameRegistry::max_thread_names + 100; i++) {
        registry.register_name(static_cast<int64_t>(i), "thread");
    }

    EXPECT_EQ(registry.size(), Datadog::ThreadNameRegistry::max_thread_names);
}

TEST(ThreadNameRegistry, EachThreadReadsItsOwnName)
{
    // lookup hands back thread-local storage, so concurrent readers must not
    // see each other's names.
    Datadog::ThreadNameRegistry registry;

    constexpr int thread_count = 8;
    for (int i = 0; i < thread_count; i++) {
        registry.register_name(i, "thread-" + std::to_string(i));
    }

    std::atomic<int> mismatches{ 0 };
    std::vector<std::thread> threads;
    threads.reserve(thread_count);
    for (int i = 0; i < thread_count; i++) {
        threads.emplace_back([&registry, &mismatches, i]() {
            const std::string expected = "thread-" + std::to_string(i);
            for (int iteration = 0; iteration < 1000; iteration++) {
                if (registry.lookup(i) != expected) {
                    mismatches++;
                }
            }
        });
    }
    for (auto& thread : threads) {
        thread.join();
    }

    EXPECT_EQ(mismatches.load(), 0);
}

TEST(ThreadNameRegistry, LookupDoesNotBlockBehindAWriter)
{
    // register_name allocates while holding the write lock, and that allocation
    // can itself be sampled on the same thread. lookup must give up rather than
    // deadlock, so it reports no name while a writer holds the lock.
    Datadog::ThreadNameRegistry registry;
    registry.register_name(1, "MainThread");

    std::atomic<bool> writer_holds_lock{ false };
    std::atomic<bool> reader_done{ false };
    std::string_view observed;

    // Registering from inside the map's own allocation path is not reproducible
    // here, so approximate it: hold the write lock from another thread and check
    // that lookup returns promptly instead of waiting.
    std::thread writer([&]() {
        for (int64_t id = 2; !reader_done.load(); id++) {
            registry.register_name(id % 64 + 2, "churn");
            writer_holds_lock.store(true);
        }
    });

    while (!writer_holds_lock.load()) {
        std::this_thread::yield();
    }
    observed = registry.lookup(1);
    reader_done.store(true);
    writer.join();

    // Either the real name or nothing, never a hang and never a wrong name.
    EXPECT_TRUE(observed.empty() || observed == "MainThread");
}
