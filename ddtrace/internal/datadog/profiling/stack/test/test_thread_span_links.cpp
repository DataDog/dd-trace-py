#include "thread_span_links.hpp"

#include <gmock/gmock.h>
#include <gtest/gtest.h>

#include <optional>
#include <random>
#include <string>
#include <thread>
#include <unordered_set>

static void
get()
{
    for (int i = 0; i < 100; i++) {
        std::string span_type;
        for (int j = 0; j < i; j++) {
            span_type.append("a");
        }
        Datadog::ThreadSpanLinks::get_instance().link_span(42, 1, 2, span_type);
    }
}

static std::string
set()
{
    std::string s;
    for (int i = 0; i < 100; i++) {
        auto thing = Datadog::ThreadSpanLinks::get_instance().get_active_span_from_thread_id(42);
        if (!thing) {
            continue;
        }
        s = thing->span_type;
    }
    return s;
}

TEST(ThreadSpanLinksConcurrency, GetSetRace)
{
    std::thread t1(get);
    std::thread t2(set);
    t1.join();
    t2.join();
}

TEST(ThreadSpanLinks, UnlinkOnlyMatchingSpan)
{
    auto& links = Datadog::ThreadSpanLinks::get_instance();
    constexpr uint64_t thread_id = 43;
    constexpr uint64_t span_id = 100;

    links.link_span(thread_id, span_id, 200, "web");
    links.unlink_span(thread_id, span_id + 1);

    EXPECT_EQ(links.get_active_span_from_thread_id(thread_id), Datadog::Span(span_id, 200, "web"));

    links.unlink_span(thread_id, span_id);
    EXPECT_EQ(links.get_active_span_from_thread_id(thread_id), std::nullopt);
}

TEST(ThreadSpanLinks, ClearFinished)
{
    unsigned int num_thread_ids = 100;
    std::unordered_set<uint64_t> thread_ids;

    std::random_device rd;
    std::mt19937 gen(rd());
    std::uniform_int_distribution<uint64_t> dis(0, UINT64_MAX);

    // Generate random 100 native thread ids
    for (unsigned int i = 0; i < num_thread_ids; i++) {
        thread_ids.insert(dis(gen));
    }

    // Call link_span with the thread ids
    for (auto thread_id : thread_ids) {
        Datadog::ThreadSpanLinks::get_instance().link_span(thread_id, thread_id, thread_id, "test");
    }

    std::unordered_set<uint64_t> finished_threads;
    std::uniform_real_distribution<double> real_dis(0, 1);

    for (auto thread_id : thread_ids) {
        if (real_dis(gen) < 0.5) {
            finished_threads.insert(thread_id);
            Datadog::ThreadSpanLinks::get_instance().unlink_span(thread_id);
        }
    }

    // Check that the unseen ids are removed
    for (auto thread_id : thread_ids) {
        std::optional<Datadog::Span> span_opt =
          Datadog::ThreadSpanLinks::get_instance().get_active_span_from_thread_id(thread_id);
        if (finished_threads.find(thread_id) == finished_threads.end()) {
            EXPECT_EQ(span_opt, Datadog::Span(thread_id, thread_id, "test"));

        } else {
            EXPECT_EQ(span_opt, std::nullopt);
        }
    }
}

TEST(ThreadSpanLinks, UnlinkFinishedSpanAcrossThreads)
{
    auto& links = Datadog::ThreadSpanLinks::get_instance();
    links.reset();

    links.link_span(100, 1000, 1000, "web");
    links.link_span(101, 1001, 1000, "web");
    links.link_span(102, 1001, 1000, "web");
    links.link_span(103, 2001, 2000, "worker");

    links.unlink_finished_span(1000);
    EXPECT_EQ(links.get_active_span_from_thread_id(100), std::nullopt);
    EXPECT_EQ(links.get_active_span_from_thread_id(101), Datadog::Span(1001, 1000, "web"));
    EXPECT_EQ(links.get_active_span_from_thread_id(102), Datadog::Span(1001, 1000, "web"));
    EXPECT_EQ(links.get_active_span_from_thread_id(103), Datadog::Span(2001, 2000, "worker"));

    links.unlink_finished_span(1001);
    EXPECT_EQ(links.get_active_span_from_thread_id(101), std::nullopt);
    EXPECT_EQ(links.get_active_span_from_thread_id(102), std::nullopt);
    EXPECT_EQ(links.get_active_span_from_thread_id(103), Datadog::Span(2001, 2000, "worker"));
}

TEST(ThreadSpanLinks, RelinkUpdatesFinishedSpanIndex)
{
    auto& links = Datadog::ThreadSpanLinks::get_instance();
    links.reset();

    links.link_span(201, 3001, 3000, "old");
    links.link_span(201, 4001, 4000, "new");

    links.unlink_finished_span(3000);
    EXPECT_EQ(links.get_active_span_from_thread_id(201), Datadog::Span(4001, 4000, "new"));
    links.unlink_finished_span(4001);
    EXPECT_EQ(links.get_active_span_from_thread_id(201), std::nullopt);
}

TEST(ThreadSpanLinks, FinishingWhileLinkingDefersCleanup)
{
    auto& links = Datadog::ThreadSpanLinks::get_instance();
    constexpr uint64_t thread_id = 301;
    constexpr uint64_t span_id = 3001;
    links.reset();

    links.on_link_start(span_id);
    EXPECT_FALSE(links.on_span_finish(span_id));

    links.link_span(thread_id, span_id, span_id, "web");
    EXPECT_TRUE(links.on_link_end(span_id));

    links.unlink_finished_span(span_id);
    EXPECT_EQ(links.get_active_span_from_thread_id(thread_id), std::nullopt);
}

int
main(int argc, char** argv)
{
    ::testing::InitGoogleTest(&argc, argv);
    (void)(::testing::GTEST_FLAG(death_test_style) = "threadsafe");
    return RUN_ALL_TESTS();
}
