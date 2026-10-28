#include "span_links.hpp"

#include <gmock/gmock.h>
#include <gtest/gtest.h>

#include <atomic>
#include <future>
#include <mutex>
#include <optional>
#include <random>
#include <string>
#include <thread>
#include <unordered_set>
#include <vector>

static void
get(uint64_t token)
{
    for (int i = 0; i < 100; i++) {
        std::string span_type;
        for (int j = 0; j < i; j++) {
            span_type.append("a");
        }
        Datadog::SpanLinks::get_instance().link(token, 42, 0, 0, Datadog::Span(1, 2, span_type));
    }
}

static std::string
set()
{
    std::string s;
    for (int i = 0; i < 100; i++) {
        auto thing = Datadog::SpanLinks::get_instance().get_active_span_from_thread_id(42);
        if (!thing) {
            continue;
        }
        s = thing->span_type;
    }
    return s;
}

TEST(SpanLinksConcurrency, GetSetRace)
{
    auto& links = Datadog::SpanLinks::get_instance();
    links.open();
    std::thread t1(get, links.lifecycle());
    std::thread t2(set);
    t1.join();
    t2.join();
    links.close();
}

TEST(SpanLinks, ThreadExitErasesOnlyThatThreadsKey)
{
    auto& links = Datadog::SpanLinks::get_instance();
    links.open();
    const uint64_t token = links.lifecycle();

    unsigned int num_thread_ids = 100;
    std::unordered_set<uint64_t> thread_ids;

    std::random_device rd;
    std::mt19937 gen(rd());
    std::uniform_int_distribution<uint64_t> dis(0, UINT64_MAX);

    // Generate random 100 native thread ids
    for (unsigned int i = 0; i < num_thread_ids; i++) {
        thread_ids.insert(dis(gen));
    }

    // Link a span to each thread id
    for (auto thread_id : thread_ids) {
        links.link(token, thread_id, 0, 0, Datadog::Span(thread_id, thread_id, "test"));
    }

    std::unordered_set<uint64_t> finished_threads;
    std::uniform_real_distribution<double> real_dis(0, 1);

    for (auto thread_id : thread_ids) {
        if (real_dis(gen) < 0.5) {
            finished_threads.insert(thread_id);
            links.unlink_span(thread_id);
        }
    }

    // Check that the unseen ids are removed
    for (auto thread_id : thread_ids) {
        std::optional<Datadog::Span> span_opt = links.get_active_span_from_thread_id(thread_id);
        if (finished_threads.find(thread_id) == finished_threads.end()) {
            EXPECT_EQ(span_opt, Datadog::Span(thread_id, thread_id, "test"));

        } else {
            EXPECT_EQ(span_opt, std::nullopt);
        }
    }
    links.close();
}

TEST(SpanLinks, FinishedSpanClearsOnlyItsOwnThreadKeys)
{
    auto& links = Datadog::SpanLinks::get_instance();
    links.open();
    const uint64_t token = links.lifecycle();

    links.link(token, 100, 0, 0, Datadog::Span(1000, 1000, "web"));
    links.link(token, 101, 0, 0, Datadog::Span(1001, 1000, "web"));
    links.link(token, 102, 0, 0, Datadog::Span(1001, 1000, "web"));
    links.link(token, 103, 0, 0, Datadog::Span(2001, 2000, "worker"));

    // Threads 101 and 102 run a child of 1000. The local root finishing first must not clear them.
    links.retarget(token, 1000, std::nullopt);
    EXPECT_EQ(links.get_active_span_from_thread_id(100), std::nullopt);
    EXPECT_EQ(links.get_active_span_from_thread_id(101), Datadog::Span(1001, 1000, "web"));
    EXPECT_EQ(links.get_active_span_from_thread_id(102), Datadog::Span(1001, 1000, "web"));
    EXPECT_EQ(links.get_active_span_from_thread_id(103), Datadog::Span(2001, 2000, "worker"));

    links.retarget(token, 1001, std::nullopt);
    EXPECT_EQ(links.get_active_span_from_thread_id(101), std::nullopt);
    EXPECT_EQ(links.get_active_span_from_thread_id(102), std::nullopt);
    EXPECT_EQ(links.get_active_span_from_thread_id(103), Datadog::Span(2001, 2000, "worker"));
    links.close();
}

namespace {

using Datadog::run_locked;
using Datadog::Span;
using Datadog::SpanLinkDomain;
using Datadog::SpanLinkEntry;
using Datadog::SpanLinkKey;
using Datadog::SpanLinkKind;
using Datadog::SpanLinks;

constexpr SpanLinkKey
thread_key(uint64_t id)
{
    return { SpanLinkDomain::Thread, id };
}

constexpr SpanLinkKey
task_key(uint64_t id)
{
    return { SpanLinkDomain::AsyncioTask, id };
}

constexpr SpanLinkKey
greenlet_key(uint64_t id)
{
    return { SpanLinkDomain::Greenlet, id };
}

Span
span_link(uint64_t span_id, uint64_t local_root_span_id = 1)
{
    return Span(span_id, local_root_span_id, "web", SpanLinkKind::Span);
}

Span
context_link(uint64_t span_id, uint64_t local_root_span_id = 1)
{
    return Span(span_id, local_root_span_id, "web", SpanLinkKind::Context);
}

std::optional<Span>
span_at(SpanLinks& links, SpanLinkKey key)
{
    auto entry = links.get_entry(key);
    return entry ? entry->span : std::nullopt;
}

uint64_t
birth_of(SpanLinks& links, SpanLinkKey key)
{
    auto entry = links.get_entry(key);
    EXPECT_TRUE(entry.has_value());
    return entry ? entry->birth : 0;
}

// Opens a fresh lifecycle and returns its token.
uint64_t
fresh_lifecycle(SpanLinks& links)
{
    links.close();
    links.open();
    return links.lifecycle();
}

} // namespace

TEST(SpanLinksKeyed, TokenIsOddOnlyWhileOpen)
{
    auto& links = SpanLinks::get_instance();

    links.close();
    const uint64_t closed = links.lifecycle();
    EXPECT_EQ(closed % 2, 0U);

    links.open();
    const uint64_t opened = links.lifecycle();
    EXPECT_EQ(opened % 2, 1U);
    EXPECT_GT(opened, closed);

    // Opening again moves to the next odd value, so a token from the previous lifecycle never matches.
    links.open();
    EXPECT_EQ(links.lifecycle(), opened + 2);

    links.close();
    EXPECT_EQ(links.lifecycle(), opened + 3);
    links.close();
    EXPECT_EQ(links.lifecycle(), opened + 5);
}

TEST(SpanLinksKeyed, WritesNeedTheCurrentOddToken)
{
    auto& links = SpanLinks::get_instance();
    const uint64_t token = fresh_lifecycle(links);

    EXPECT_FALSE(links.link(token + 1, std::nullopt, 1, 0, span_link(10)));
    EXPECT_FALSE(links.link(token - 2, std::nullopt, 1, 0, span_link(10)));
    EXPECT_EQ(links.get_entry(task_key(1)), std::nullopt);

    EXPECT_TRUE(links.link(token, std::nullopt, 1, 0, span_link(10)));
    EXPECT_EQ(span_at(links, task_key(1)), span_link(10));

    // A stop makes the token stale and clears every key, and while stopped the even token is rejected too.
    links.close();
    const uint64_t closed = links.lifecycle();
    EXPECT_EQ(links.get_entry(task_key(1)), std::nullopt);
    EXPECT_FALSE(links.link(token, std::nullopt, 1, 0, span_link(10)));
    EXPECT_FALSE(links.link(closed, std::nullopt, 1, 0, span_link(10)));
    EXPECT_FALSE(links.retarget(closed, 10, std::nullopt));

    // A token from before a stop and a start does not match the new lifecycle.
    links.open();
    EXPECT_FALSE(links.link(token, std::nullopt, 1, 0, span_link(10)));
    EXPECT_FALSE(links.retarget(token, 10, std::nullopt));
    EXPECT_EQ(links.get_entry(task_key(1)), std::nullopt);
    EXPECT_FALSE(links.holds_span(10));

    links.close();
}

TEST(SpanLinksKeyed, DomainsAreSeparate)
{
    auto& links = SpanLinks::get_instance();
    const uint64_t token = fresh_lifecycle(links);
    constexpr uint64_t shared_id = 42;

    links.link(token, shared_id, 0, 0, span_link(1));
    links.link(token, std::nullopt, shared_id, 0, span_link(2));
    links.link(token, std::nullopt, 0, shared_id, span_link(3));

    EXPECT_EQ(span_at(links, thread_key(shared_id)), span_link(1));
    EXPECT_EQ(links.get_active_span_from_thread_id(shared_id), span_link(1));
    EXPECT_EQ(span_at(links, task_key(shared_id)), span_link(2));
    EXPECT_EQ(span_at(links, greenlet_key(shared_id)), span_link(3));

    links.clear(task_key(shared_id));
    EXPECT_EQ(links.get_entry(task_key(shared_id)), std::nullopt);
    EXPECT_EQ(span_at(links, thread_key(shared_id)), span_link(1));
    EXPECT_EQ(span_at(links, greenlet_key(shared_id)), span_link(3));
    EXPECT_FALSE(links.holds_span(2));

    links.close();
}

TEST(SpanLinksKeyed, OneLinkWritesEveryRequestedKey)
{
    auto& links = SpanLinks::get_instance();
    const uint64_t token = fresh_lifecycle(links);

    // Zero task and greenlet ids mean "no key".
    EXPECT_TRUE(links.link(token, 5, 0, 0, span_link(100)));
    EXPECT_EQ(links.get_entry(task_key(0)), std::nullopt);
    EXPECT_EQ(links.get_entry(greenlet_key(0)), std::nullopt);

    EXPECT_TRUE(links.link(token, 5, 6, 7, span_link(101)));
    EXPECT_EQ(span_at(links, thread_key(5)), span_link(101));
    EXPECT_EQ(span_at(links, task_key(6)), span_link(101));
    EXPECT_EQ(span_at(links, greenlet_key(7)), span_link(101));
    EXPECT_FALSE(links.holds_span(100));
    EXPECT_TRUE(links.holds_span(101));

    links.close();
}

TEST(SpanLinksKeyed, NoSpanErasesThreadsAndLeavesLogicalTombstones)
{
    auto& links = SpanLinks::get_instance();
    const uint64_t token = fresh_lifecycle(links);

    // On a missing logical key, "no span" creates nothing, so untraced tasks cost no entry.
    links.link(token, std::nullopt, 1, 2, std::nullopt);
    EXPECT_EQ(links.get_entry(task_key(1)), std::nullopt);
    EXPECT_EQ(links.get_entry(greenlet_key(2)), std::nullopt);

    links.link(token, 9, 1, 2, span_link(10));
    const uint64_t task_birth = birth_of(links, task_key(1));
    const uint64_t greenlet_birth = birth_of(links, greenlet_key(2));

    links.link(token, 9, 1, 2, std::nullopt);
    EXPECT_EQ(links.get_entry(thread_key(9)), std::nullopt);
    EXPECT_EQ(links.get_entry(task_key(1)), (SpanLinkEntry{ std::nullopt, task_birth }));
    EXPECT_EQ(links.get_entry(greenlet_key(2)), (SpanLinkEntry{ std::nullopt, greenlet_birth }));
    EXPECT_FALSE(links.holds_span(10));

    // clear physically removes a tombstone.
    links.clear(task_key(1));
    EXPECT_EQ(links.get_entry(task_key(1)), std::nullopt);

    links.close();
}

TEST(SpanLinksKeyed, BirthIsFixedForTheKeyLife)
{
    auto& links = SpanLinks::get_instance();
    const uint64_t token = fresh_lifecycle(links);

    const uint64_t before = links.birth_watermark();
    links.link(token, std::nullopt, 1, 0, span_link(10));
    const uint64_t birth = birth_of(links, task_key(1));
    EXPECT_GT(birth, before);
    EXPECT_EQ(links.birth_watermark(), birth);

    // Overwrites, Context links, tombstones and retargets keep the birth and do not advance the counter.
    links.link(token, std::nullopt, 1, 0, span_link(11));
    links.link(token, std::nullopt, 1, 0, context_link(12));
    links.link(token, std::nullopt, 1, 0, std::nullopt);
    links.link(token, std::nullopt, 1, 0, span_link(13));
    links.retarget(token, 13, span_link(14));
    links.retarget(token, 14, std::nullopt);
    links.link(token, std::nullopt, 1, 0, span_link(15));
    EXPECT_EQ(birth_of(links, task_key(1)), birth);
    EXPECT_EQ(links.birth_watermark(), birth);

    // Thread keys never carry a birth.
    links.link(token, 3, 0, 0, span_link(16));
    EXPECT_EQ(birth_of(links, thread_key(3)), 0U);
    EXPECT_EQ(links.birth_watermark(), birth);

    // Only physical removal makes the next write create a new key with a new birth.
    links.clear(task_key(1));
    links.link(token, std::nullopt, 1, 0, span_link(17));
    EXPECT_GT(birth_of(links, task_key(1)), birth);

    // A stop and a start remove every key but keep the counter monotonic.
    const uint64_t last = links.birth_watermark();
    const uint64_t next_token = fresh_lifecycle(links);
    links.link(next_token, std::nullopt, 1, 0, span_link(18));
    EXPECT_GT(birth_of(links, task_key(1)), last);

    links.close();
}

TEST(SpanLinksKeyed, OverwriteMovesTheIndex)
{
    auto& links = SpanLinks::get_instance();
    const uint64_t token = fresh_lifecycle(links);

    links.link(token, std::nullopt, 1, 0, span_link(10));
    links.link(token, std::nullopt, 1, 0, span_link(20));
    EXPECT_FALSE(links.holds_span(10));
    EXPECT_TRUE(links.holds_span(20));

    // A retarget of the old span cannot overwrite the newer value.
    links.retarget(token, 10, std::nullopt);
    EXPECT_EQ(span_at(links, task_key(1)), span_link(20));

    links.close();
}

TEST(SpanLinksKeyed, ContextLinksAreNotIndexed)
{
    auto& links = SpanLinks::get_instance();
    const uint64_t token = fresh_lifecycle(links);

    links.link(token, 4, 1, 0, context_link(10));
    EXPECT_FALSE(links.holds_span(10));

    // The finish of the span with the same id leaves Context links alone.
    links.retarget(token, 10, std::nullopt);
    EXPECT_EQ(span_at(links, thread_key(4)), context_link(10));
    EXPECT_EQ(span_at(links, task_key(1)), context_link(10));

    // Switching a key between kinds keeps the index exact.
    links.link(token, 4, 0, 0, span_link(10));
    EXPECT_TRUE(links.holds_span(10));
    links.link(token, 4, 0, 0, context_link(10));
    EXPECT_FALSE(links.holds_span(10));

    links.close();
}

TEST(SpanLinksKeyed, RetargetWithReplacement)
{
    auto& links = SpanLinks::get_instance();
    const uint64_t token = fresh_lifecycle(links);

    links.link(token, 1, 2, 3, span_link(10, 1));
    links.link(token, std::nullopt, 4, 0, context_link(10, 1));
    links.link(token, std::nullopt, 5, 0, span_link(20, 1));
    links.link(token, std::nullopt, 6, 0, span_link(30, 1));

    EXPECT_TRUE(links.retarget(token, 10, span_link(30, 1)));
    EXPECT_EQ(span_at(links, thread_key(1)), span_link(30, 1));
    EXPECT_EQ(span_at(links, task_key(2)), span_link(30, 1));
    EXPECT_EQ(span_at(links, greenlet_key(3)), span_link(30, 1));
    EXPECT_EQ(span_at(links, task_key(4)), context_link(10, 1));
    EXPECT_EQ(span_at(links, task_key(5)), span_link(20, 1));
    EXPECT_EQ(span_at(links, task_key(6)), span_link(30, 1));
    EXPECT_FALSE(links.holds_span(10));
    EXPECT_TRUE(links.holds_span(30));

    // Idempotent: nothing holds 10 any more.
    EXPECT_TRUE(links.retarget(token, 10, span_link(20, 1)));
    EXPECT_EQ(span_at(links, task_key(2)), span_link(30, 1));

    // The moved keys follow their new span's finish.
    EXPECT_TRUE(links.retarget(token, 30, std::nullopt));
    EXPECT_EQ(links.get_entry(thread_key(1)), std::nullopt);
    EXPECT_EQ(span_at(links, task_key(2)), std::nullopt);
    EXPECT_EQ(span_at(links, greenlet_key(3)), std::nullopt);
    EXPECT_EQ(span_at(links, task_key(6)), std::nullopt);
    EXPECT_TRUE(links.get_entry(task_key(6)).has_value());
    EXPECT_EQ(span_at(links, task_key(5)), span_link(20, 1));
    EXPECT_FALSE(links.holds_span(30));

    links.close();
}

TEST(SpanLinksKeyed, RetargetToAContextIsNotIndexed)
{
    auto& links = SpanLinks::get_instance();
    const uint64_t token = fresh_lifecycle(links);

    links.link(token, 1, 2, 0, span_link(10));
    EXPECT_TRUE(links.retarget(token, 10, context_link(20)));
    EXPECT_EQ(span_at(links, thread_key(1)), context_link(20));
    EXPECT_EQ(span_at(links, task_key(2)), context_link(20));
    EXPECT_FALSE(links.holds_span(10));
    EXPECT_FALSE(links.holds_span(20));

    links.close();
}

TEST(SpanLinksKeyed, RetargetToTheSameSpanIdOnlyUpdatesLabels)
{
    auto& links = SpanLinks::get_instance();
    const uint64_t token = fresh_lifecycle(links);

    links.link(token, std::nullopt, 1, 0, span_link(10, 1));
    EXPECT_TRUE(links.retarget(token, 10, span_link(10, 2)));
    EXPECT_EQ(span_at(links, task_key(1)), span_link(10, 2));
    EXPECT_TRUE(links.holds_span(10));

    links.close();
}

TEST(SpanLinksKeyed, UncontendedCallsSkipContentionHook)
{
    auto& links = SpanLinks::get_instance();
    const uint64_t token = fresh_lifecycle(links);

    int calls = 0;
    auto counting = [&calls](auto&& fn) {
        ++calls;
        fn();
    };
    EXPECT_TRUE(links.link(token, std::nullopt, 1, 0, span_link(10), counting));
    EXPECT_TRUE(links.holds_span(10, counting));
    EXPECT_EQ(links.get_entry(task_key(1), counting), (SpanLinkEntry{ span_link(10), birth_of(links, task_key(1)) }));
    EXPECT_EQ(calls, 0);

    links.close();
}

TEST(RunLocked, ContendedCallGoesThroughHook)
{
    // Another thread holds the mutex until the hook runs, so the call is always contended.
    std::mutex mtx;
    std::promise<void> locked;
    std::promise<void> release;
    std::thread holder([&] {
        const std::lock_guard<std::mutex> guard(mtx);
        locked.set_value();
        release.get_future().wait();
    });
    locked.get_future().wait();

    int calls = 0;
    bool ran = false;
    run_locked(
      mtx,
      [&] { ran = true; },
      [&](auto&& fn) {
          ++calls;
          // The work has not run before the hook, and runs inside the function the hook gets.
          EXPECT_FALSE(ran);
          release.set_value();
          fn();
          EXPECT_TRUE(ran);
      });
    holder.join();

    EXPECT_EQ(calls, 1);
    EXPECT_TRUE(ran);
    // The hook's function released the mutex before returning.
    ASSERT_TRUE(mtx.try_lock());
    mtx.unlock();
}

TEST(SpanLinksKeyed, WritersAfterCloseNeverLand)
{
    auto& links = SpanLinks::get_instance();
    fresh_lifecycle(links);

    constexpr int writers = 4;
    constexpr uint64_t keys_per_writer = 2000;
    std::atomic<bool> closed{ false };
    std::atomic<int> accepted{ 0 };
    std::vector<std::thread> threads;
    for (int w = 0; w < writers; ++w) {
        threads.emplace_back([&links, &closed, &accepted, w] {
            for (uint64_t i = 1; i <= keys_per_writer; ++i) {
                // Read the token first, as Python writers do, then write.
                const uint64_t token = links.lifecycle();
                const uint64_t id = (static_cast<uint64_t>(w) << 32U) | i;
                if (links.link(token, std::nullopt, id, id, span_link(id))) {
                    accepted.fetch_add(1);
                }
                if (i % 7 == 0) {
                    links.retarget(token, id, span_link(id + 1));
                }
                if (closed.load() && i > keys_per_writer / 2) {
                    break;
                }
            }
        });
    }

    // Close while writers are running, after some of their writes landed.
    while (accepted.load() < writers) {
        std::this_thread::yield();
    }
    links.close();
    closed.store(true);
    for (auto& t : threads) {
        t.join();
    }

    for (int w = 0; w < writers; ++w) {
        for (uint64_t i = 1; i <= keys_per_writer; ++i) {
            const uint64_t id = (static_cast<uint64_t>(w) << 32U) | i;
            ASSERT_EQ(links.get_entry(task_key(id)), std::nullopt);
            ASSERT_EQ(links.get_entry(greenlet_key(id)), std::nullopt);
            ASSERT_FALSE(links.holds_span(id));
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
