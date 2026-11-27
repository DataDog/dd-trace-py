#include "echion/echion_sampler.h"

#include <gtest/gtest.h>

#include <unordered_set>

TEST(TaskLinks, LinkSurvivesOneSnapshotWithoutChild)
{
    // The child was created and linked by gather after the snapshot was taken, so the snapshot does not have it yet.
    EchionSampler echion;
    PyObject parent{};
    PyObject child{};

    echion.link_task(&parent, &child, 1);
    echion.prune_task_links(1, { &parent });
    ASSERT_EQ(echion.task_link_map().count(&child), 1u);
    EXPECT_EQ(echion.task_link_map()[&child].parent, &parent);

    echion.prune_task_links(1, { &parent, &child });
    echion.prune_task_links(1, { &parent });
    EXPECT_EQ(echion.task_link_map().count(&child), 1u);
}

TEST(TaskLinks, LinkIsRemovedAfterTwoSnapshotsWithoutChild)
{
    EchionSampler echion;
    PyObject parent{};
    PyObject child{};

    echion.link_task(&parent, &child, 1);
    echion.prune_task_links(1, { &parent, &child });
    echion.prune_task_links(1, { &parent });
    echion.prune_task_links(1, { &parent });
    EXPECT_EQ(echion.task_link_map().count(&child), 0u);
}

TEST(TaskLinks, LinkOfChildNeverSeenIsRemoved)
{
    // A short-lived gather child ran and finished between two snapshots, so no snapshot ever had it. Its link must
    // not stay in the map forever: the sampler walks the whole map on every sample.
    EchionSampler echion;
    PyObject parent{};
    PyObject children[10]{};

    for (auto& child : children) {
        echion.link_task(&parent, &child, 1);
    }
    echion.prune_task_links(1, { &parent });
    echion.prune_task_links(1, { &parent });
    EXPECT_TRUE(echion.task_link_map().empty());
}

TEST(TaskLinks, NewLinkForReusedAddressGetsFullGracePeriod)
{
    // A finished child was missing from one snapshot. A new Task at the same address is linked before the next
    // snapshot, which misses it as well.
    EchionSampler echion;
    PyObject old_parent{};
    PyObject new_parent{};
    PyObject child{};

    echion.link_task(&old_parent, &child, 1);
    echion.prune_task_links(1, { &old_parent, &new_parent });
    echion.link_task(&new_parent, &child, 1);
    echion.prune_task_links(1, { &old_parent, &new_parent });
    ASSERT_EQ(echion.task_link_map().count(&child), 1u);
    EXPECT_EQ(echion.task_link_map()[&child].parent, &new_parent);
}

TEST(TaskLinks, StrongAndWeakLinksArePrunedIndependently)
{
    // The same child can have both a gather link and a create_task link. Missing it from one snapshot must start the
    // grace period of both links, not let one consume the grace period of the other.
    EchionSampler echion;
    PyObject parent{};
    PyObject child{};

    echion.link_task(&parent, &child, 1);
    echion.weak_link_task(&parent, &child, 1);
    echion.prune_task_links(1, { &parent });
    echion.prune_weak_task_links(1, { &parent });
    EXPECT_EQ(echion.task_link_map().count(&child), 1u);
    EXPECT_EQ(echion.weak_task_link_map().count(&child), 1u);

    echion.prune_task_links(1, { &parent });
    echion.prune_weak_task_links(1, { &parent });
    EXPECT_EQ(echion.task_link_map().count(&child), 0u);
    EXPECT_EQ(echion.weak_task_link_map().count(&child), 0u);
}

TEST(TaskLinks, OtherLoopSnapshotsDoNotStartGracePeriod)
{
    EchionSampler echion;
    PyObject parent{};
    PyObject child{};

    echion.link_task(&parent, &child, 2);
    // Thread subsampling can select loop 1 twice before loop 2 is sampled at all.
    echion.prune_task_links(1, {});
    echion.prune_task_links(1, {});
    ASSERT_EQ(echion.task_link_map().count(&child), 1u);
    EXPECT_EQ(echion.task_link_map()[&child].parent, &parent);

    // Only snapshots of the child's own loop count toward expiration.
    echion.prune_task_links(2, { &parent });
    EXPECT_EQ(echion.task_link_map().count(&child), 1u);
    echion.prune_task_links(2, { &parent });
    EXPECT_TRUE(echion.task_link_map().empty());
}

TEST(TaskLinks, OtherLoopSnapshotsDoNotConsumeGracePeriod)
{
    EchionSampler echion;
    PyObject parent{};
    PyObject child{};
    const std::unordered_set<PyObject*> snapshot{ &parent };

    // The child is linked after loop 2 takes its snapshot but before it prunes.
    echion.link_task(&parent, &child, 2);
    echion.prune_task_links(2, snapshot);
    echion.prune_task_links(1, {});
    echion.prune_task_links(1, {});
    ASSERT_EQ(echion.task_link_map().count(&child), 1u);

    echion.prune_task_links(2, { &parent, &child });
    echion.prune_task_links(2, { &parent });
    EXPECT_EQ(echion.task_link_map().count(&child), 1u);
    echion.prune_task_links(2, { &parent });
    EXPECT_TRUE(echion.task_link_map().empty());
}

TEST(TaskLinks, RelinkedAddressBelongsToNewLoop)
{
    EchionSampler echion;
    PyObject parent{};
    PyObject child{};

    echion.link_task(&parent, &child, 1);
    echion.prune_task_links(1, {});
    echion.link_task(&parent, &child, 2);
    echion.prune_task_links(1, {});
    echion.prune_task_links(1, {});
    echion.prune_task_links(2, {});
    EXPECT_EQ(echion.task_link_map().count(&child), 1u);
    echion.prune_task_links(2, {});
    EXPECT_TRUE(echion.task_link_map().empty());
}

TEST(TaskLinks, UntrackedLoopsAgeIndependentlyOfSnapshots)
{
    EchionSampler echion;
    PyObject parent{};
    PyObject tracked_child{};
    PyObject untracked_child{};
    PyObject retracked_child{};

    echion.link_task(&parent, &tracked_child, 1);
    echion.weak_link_task(&parent, &tracked_child, 1);
    echion.link_task(&parent, &untracked_child, 2);
    echion.weak_link_task(&parent, &untracked_child, 2);
    echion.link_task(&parent, &retracked_child, 3);
    echion.weak_link_task(&parent, &retracked_child, 3);

    echion.prune_inactive_loop_task_links({ 1 });
    EXPECT_EQ(echion.task_link_map().size(), 3u);
    EXPECT_EQ(echion.weak_task_link_map().size(), 3u);
    echion.prune_inactive_loop_task_links({ 1, 3 });
    EXPECT_EQ(echion.task_link_map().count(&untracked_child), 0u);
    EXPECT_EQ(echion.weak_task_link_map().count(&untracked_child), 0u);
    EXPECT_EQ(echion.task_link_map().count(&tracked_child), 1u);
    EXPECT_EQ(echion.weak_task_link_map().count(&tracked_child), 1u);

    // A live cycle resets the consecutive-miss counter.
    echion.prune_inactive_loop_task_links({ 1 });
    EXPECT_EQ(echion.task_link_map().count(&retracked_child), 1u);
    EXPECT_EQ(echion.weak_task_link_map().count(&retracked_child), 1u);

    // A miss while untracked must not consume the grace period of a real snapshot.
    echion.prune_task_links(3, {});
    echion.prune_weak_task_links(3, {});
    EXPECT_EQ(echion.task_link_map().count(&retracked_child), 1u);
    EXPECT_EQ(echion.weak_task_link_map().count(&retracked_child), 1u);
}

TEST(TaskLinks, LinksForNeverRegisteredLoopDoNotAccumulate)
{
    EchionSampler echion;
    PyObject parent{};
    PyObject children[4][10]{};

    for (auto& batch : children) {
        for (auto& child : batch) {
            echion.link_task(&parent, &child, 1);
            echion.weak_link_task(&parent, &child, 1);
        }
        echion.prune_inactive_loop_task_links({});
        EXPECT_EQ(echion.task_link_map().size(), 10u);
        EXPECT_EQ(echion.weak_task_link_map().size(), 10u);
    }

    echion.prune_inactive_loop_task_links({});
    EXPECT_TRUE(echion.task_link_map().empty());
    EXPECT_TRUE(echion.weak_task_link_map().empty());
}

TEST(TaskLinks, ReusedThreadIdDoesNotKeepLoopLive)
{
    ThreadInfo thread(1, 2, "exited-thread", 0);
    thread.asyncio_loop = 3;
    PyThreadState tstate{};
    tstate.thread_id = thread.thread_id;
#if PY_VERSION_HEX >= 0x030b0000
    thread.asyncio_loop_native_id = 4;
    tstate.native_thread_id = thread.asyncio_loop_native_id;
#else
    thread.asyncio_loop_interp_id = 0;
    thread.asyncio_loop_tstate_id = 1;
    tstate.id = thread.asyncio_loop_tstate_id;
#endif
    ASSERT_EQ(thread.live_asyncio_loop(tstate, 0), 3u);

#if PY_VERSION_HEX >= 0x030b0000
    tstate.native_thread_id = thread.native_id;
#else
    ++tstate.id;
#endif
    EXPECT_EQ(thread.live_asyncio_loop(tstate, 0), 0u);
}
