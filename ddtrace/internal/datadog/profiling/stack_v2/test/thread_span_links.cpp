#include <gtest/gtest.h>

#include <string>
#include <thread>

#include "thread_span_links.hpp"

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

int
main(int argc, char** argv)
{
    ::testing::InitGoogleTest(&argc, argv);
    (void)(::testing::GTEST_FLAG(death_test_style) = "threadsafe");
    return RUN_ALL_TESTS();
}
