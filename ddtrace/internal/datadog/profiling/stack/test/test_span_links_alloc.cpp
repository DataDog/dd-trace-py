#include "span_links.hpp"

#include <gtest/gtest.h>

#include <functional>
#include <future>
#include <initializer_list>
#include <new>
#include <optional>
#include <thread>
#include <utility>

namespace {
// Isolate allocation injection in this executable, and disable it before any assertion allocates.
thread_local int allocations_before_failure = -1;
// Called by the next allocation on this thread. Map allocations happen under SpanLinks' mutex, so this lets a test
// keep the mutex held for as long as it needs.
thread_local std::function<void()> pause_at_next_allocation;
}

// Wrap allocation calls, not the allocator itself: ASan and Valgrind must still see matching new/delete operations.
// Only calls from this executable are wrapped. The hash containers are instantiated here, so their node and bucket
// allocations can fail. std::string is compiled into libstdc++, so string copies never fail in these tests.
extern "C" void*
__real__Znwm(std::size_t size);

extern "C" void*
__wrap__Znwm(std::size_t size)
{
    if (pause_at_next_allocation) {
        const std::function<void()> pause = std::move(pause_at_next_allocation);
        pause_at_next_allocation = nullptr;
        pause();
    }
    if (allocations_before_failure == 0) {
        allocations_before_failure = -1;
        throw std::bad_alloc();
    }
    if (allocations_before_failure > 0) {
        --allocations_before_failure;
    }
    return __real__Znwm(size);
}

namespace {

using Datadog::Span;
using Datadog::SpanLinkDomain;
using Datadog::SpanLinkKey;
using Datadog::SpanLinkKind;
using Datadog::SpanLinks;

Span
span_link(uint64_t span_id)
{
    return Span(span_id, 1, "web", SpanLinkKind::Span);
}

constexpr SpanLinkKey thread_1{ SpanLinkDomain::Thread, 1 };
constexpr SpanLinkKey task_2{ SpanLinkDomain::AsyncioTask, 2 };
constexpr SpanLinkKey greenlet_3{ SpanLinkDomain::Greenlet, 3 };
constexpr SpanLinkKey task_4{ SpanLinkDomain::AsyncioTask, 4 };

std::optional<uint64_t>
span_id_at(SpanLinks& links, SpanLinkKey key)
{
    auto entry = links.get_entry(key);
    if (!entry || !entry->span) {
        return std::nullopt;
    }
    return entry->span->span_id;
}

// Every key that holds a span is indexed under it, and the index holds nothing else. A retarget to "no span" must
// then leave no key holding any of the spans.
void
expect_consistent(SpanLinks& links, uint64_t token, std::initializer_list<uint64_t> span_ids)
{
    const SpanLinkKey keys[] = { thread_1, task_2, greenlet_3, task_4 };
    for (const uint64_t span_id : span_ids) {
        bool held = false;
        for (const auto& key : keys) {
            held = held || span_id_at(links, key) == span_id;
        }
        EXPECT_EQ(links.holds_span(span_id), held) << "span " << span_id;
        links.retarget(token, span_id, std::nullopt);
        EXPECT_FALSE(links.holds_span(span_id)) << "span " << span_id;
    }
    for (const auto& key : keys) {
        EXPECT_EQ(span_id_at(links, key), std::nullopt);
    }
}

} // namespace

TEST(SpanLinksAllocation, FailedLinkLeavesNoOrphan)
{
    auto& links = SpanLinks::get_instance();
    bool succeeded = false;
    int failures = 0;
    for (int allocation = 0; allocation < 64 && !succeeded; ++allocation) {
        links.close();
        links.open();
        const uint64_t token = links.lifecycle();
        // The new keys share a span with an existing key, and the thread key moves away from another span, so a
        // rollback must keep both indexes intact.
        links.link(token, std::nullopt, 4, 0, span_link(100));
        links.link(token, 1, 0, 0, span_link(200));

        allocations_before_failure = allocation;
        try {
            links.link(token, 1, 2, 3, span_link(100));
            succeeded = true;
        } catch (const std::bad_alloc&) {
            ++failures;
        }
        allocations_before_failure = -1;

        EXPECT_EQ(span_id_at(links, task_4), 100U);
        if (succeeded) {
            EXPECT_EQ(span_id_at(links, thread_1), 100U);
            EXPECT_EQ(span_id_at(links, task_2), 100U);
            EXPECT_EQ(span_id_at(links, greenlet_3), 100U);
        }
        expect_consistent(links, token, { 100, 200 });
    }
    EXPECT_TRUE(succeeded);
    EXPECT_GE(failures, 3);
    links.close();
}

TEST(SpanLinksAllocation, FailedRetargetLeavesEveryKeyAsItWas)
{
    auto& links = SpanLinks::get_instance();
    bool succeeded = false;
    int failures = 0;
    for (int allocation = 0; allocation < 64 && !succeeded; ++allocation) {
        links.close();
        links.open();
        const uint64_t token = links.lifecycle();
        links.link(token, 1, 2, 3, span_link(100));
        links.link(token, std::nullopt, 4, 0, span_link(200));

        allocations_before_failure = allocation;
        try {
            links.retarget(token, 100, span_link(200));
            succeeded = true;
        } catch (const std::bad_alloc&) {
            ++failures;
        }
        allocations_before_failure = -1;

        const uint64_t expected = succeeded ? 200U : 100U;
        EXPECT_EQ(span_id_at(links, thread_1), expected);
        EXPECT_EQ(span_id_at(links, task_2), expected);
        EXPECT_EQ(span_id_at(links, greenlet_3), expected);
        EXPECT_EQ(span_id_at(links, task_4), 200U);
        expect_consistent(links, token, { 100, 200 });
    }
    EXPECT_TRUE(succeeded);
    EXPECT_GE(failures, 2);
    links.close();
}

TEST(SpanLinksAllocation, WriterContendingAcrossCloseIsDropped)
{
    auto& links = SpanLinks::get_instance();
    links.close();
    links.open();
    const uint64_t token = links.lifecycle();
    const Span holder_span = span_link(70);
    const Span writer_span = span_link(80);

    // The holder pauses in an allocation, inside its link's critical section, so the writer finds the mutex held.
    std::promise<void> holding;
    std::promise<void> release_holder;
    std::thread holder([&] {
        pause_at_next_allocation = [&] {
            holding.set_value();
            release_holder.get_future().wait();
        };
        links.link(token, std::nullopt, 7, 0, holder_span);
    });
    holding.get_future().wait();

    // The writer read its token before the close, and blocks on the mutex only after the close released it, as a
    // Python writer that loses the race for the mutex to links_close() does.
    std::promise<void> contended;
    std::promise<void> closed;
    bool accepted = true;
    std::thread writer([&] {
        accepted = links.link(token, std::nullopt, 8, 0, writer_span, [&](auto&& fn) {
            contended.set_value();
            closed.get_future().wait();
            fn();
        });
    });
    contended.get_future().wait();

    std::thread closer([&] { links.close(); });
    release_holder.set_value();
    holder.join();
    closer.join();
    closed.set_value();
    writer.join();

    EXPECT_FALSE(accepted);
    EXPECT_EQ(links.get_entry({ SpanLinkDomain::AsyncioTask, 7 }), std::nullopt);
    EXPECT_EQ(links.get_entry({ SpanLinkDomain::AsyncioTask, 8 }), std::nullopt);
    EXPECT_FALSE(links.holds_span(80));
}
