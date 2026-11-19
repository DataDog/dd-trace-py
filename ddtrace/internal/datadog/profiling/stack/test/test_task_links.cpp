#include "echion/echion_sampler.h"

#include <gtest/gtest.h>

#include <memory>
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

TEST(TaskLinks, ReplacingOrUntrackingLoopClearsOnlyItsLinks)
{
    EchionSampler echion;
    ThreadInfo thread(1, 1, "loop-thread", 0);
    PyObject parent{};
    PyObject child1{};
    PyObject child2{};

    echion.set_asyncio_loop(thread, 1);
    echion.link_task(&parent, &child1, 1);
    echion.weak_link_task(&parent, &child1, 1);
    echion.link_task(&parent, &child2, 2);
    echion.weak_link_task(&parent, &child2, 2);

    // Registering the same loop again must not discard links.
    echion.set_asyncio_loop(thread, 1);
    EXPECT_EQ(echion.task_link_map().size(), 2u);
    EXPECT_EQ(echion.weak_task_link_map().size(), 2u);

    echion.set_asyncio_loop(thread, 2);
    EXPECT_EQ(thread.asyncio_loop, 2u);
    EXPECT_EQ(echion.task_link_map().count(&child1), 0u);
    EXPECT_EQ(echion.weak_task_link_map().count(&child1), 0u);
    EXPECT_EQ(echion.task_link_map().count(&child2), 1u);
    EXPECT_EQ(echion.weak_task_link_map().count(&child2), 1u);

    // Thread exit and set_event_loop(None) untrack the loop, without waiting for another sample.
    echion.set_asyncio_loop(thread, 0);
    EXPECT_EQ(thread.asyncio_loop, 0u);
    EXPECT_TRUE(echion.task_link_map().empty());
    EXPECT_TRUE(echion.weak_task_link_map().empty());
}

TEST(TaskLinks, UntrackedLoopsAgeIndependentlyOfSnapshots)
{
    EchionSampler echion;
    auto& threads = echion.thread_info_map();
    auto& thread = *threads.emplace(1, std::make_unique<ThreadInfo>(1, 1, "tracked", 0)).first->second;
    PyObject parent{};
    PyObject tracked_child{};
    PyObject untracked_child{};
    PyObject retracked_child{};

    echion.set_asyncio_loop(thread, 1);
    echion.link_task(&parent, &tracked_child, 1);
    echion.weak_link_task(&parent, &tracked_child, 1);
    echion.link_task(&parent, &untracked_child, 2);
    echion.weak_link_task(&parent, &untracked_child, 2);
    echion.link_task(&parent, &retracked_child, 3);
    echion.weak_link_task(&parent, &retracked_child, 3);

    echion.prune_untracked_task_links();
    EXPECT_EQ(echion.task_link_map().size(), 3u);
    EXPECT_EQ(echion.weak_task_link_map().size(), 3u);
    auto& second = *threads.emplace(2, std::make_unique<ThreadInfo>(2, 2, "retracked", 0)).first->second;
    echion.set_asyncio_loop(second, 3);
    echion.prune_untracked_task_links();
    EXPECT_EQ(echion.task_link_map().count(&untracked_child), 0u);
    EXPECT_EQ(echion.weak_task_link_map().count(&untracked_child), 0u);
    EXPECT_EQ(echion.task_link_map().count(&tracked_child), 1u);
    EXPECT_EQ(echion.weak_task_link_map().count(&tracked_child), 1u);

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
        echion.prune_untracked_task_links();
        EXPECT_EQ(echion.task_link_map().size(), 10u);
        EXPECT_EQ(echion.weak_task_link_map().size(), 10u);
    }

    echion.prune_untracked_task_links();
    EXPECT_TRUE(echion.task_link_map().empty());
    EXPECT_TRUE(echion.weak_task_link_map().empty());
}
