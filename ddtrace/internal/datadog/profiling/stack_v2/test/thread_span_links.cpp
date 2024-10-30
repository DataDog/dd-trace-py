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

int
main(int argc, char** argv)
{
    ::testing::InitGoogleTest(&argc, argv);
    (void)(::testing::GTEST_FLAG(death_test_style) = "threadsafe");
    return RUN_ALL_TESTS();
}
