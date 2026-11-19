#include "echion/echion_sampler.h"

#include <gtest/gtest.h>

#include <unordered_set>

TEST(WeakTaskLinks, LinkSurvivesOneSnapshotWithoutChild)
{
    // The child was created and linked after the snapshot was taken, so the snapshot does not have it yet.
    EchionSampler echion;
    PyObject parent{};
    PyObject child{};

    echion.weak_link_task(&parent, &child, 1);
    echion.prune_weak_task_links(1, { &parent });
    ASSERT_EQ(echion.weak_task_link_map().count(&child), 1u);
    EXPECT_EQ(echion.weak_task_link_map()[&child].parent, &parent);

    echion.prune_weak_task_links(1, { &parent, &child });
    echion.prune_weak_task_links(1, { &parent });
    EXPECT_EQ(echion.weak_task_link_map().count(&child), 1u);
}

TEST(WeakTaskLinks, LinkIsRemovedAfterTwoSnapshotsWithoutChild)
{
    EchionSampler echion;
    PyObject parent{};
    PyObject child{};

    echion.weak_link_task(&parent, &child, 1);
    echion.prune_weak_task_links(1, { &parent, &child });
    echion.prune_weak_task_links(1, { &parent });
    echion.prune_weak_task_links(1, { &parent });
    EXPECT_EQ(echion.weak_task_link_map().count(&child), 0u);
}

TEST(WeakTaskLinks, NewLinkForReusedAddressGetsFullGracePeriod)
{
    // A finished child was missing from one snapshot. A new Task at the same address is linked before the next
    // snapshot, which misses it as well.
    EchionSampler echion;
    PyObject old_parent{};
    PyObject new_parent{};
    PyObject child{};

    echion.weak_link_task(&old_parent, &child, 1);
    echion.prune_weak_task_links(1, { &old_parent, &new_parent });
    echion.weak_link_task(&new_parent, &child, 1);
    echion.prune_weak_task_links(1, { &old_parent, &new_parent });
    ASSERT_EQ(echion.weak_task_link_map().count(&child), 1u);
    EXPECT_EQ(echion.weak_task_link_map()[&child].parent, &new_parent);
}

TEST(WeakTaskLinks, OtherLoopSnapshotsDoNotStartGracePeriod)
{
    EchionSampler echion;
    PyObject parent{};
    PyObject child{};

    echion.weak_link_task(&parent, &child, 2);
    // Thread subsampling can select loop 1 twice before loop 2 is sampled at all.
    echion.prune_weak_task_links(1, {});
    echion.prune_weak_task_links(1, {});
    ASSERT_EQ(echion.weak_task_link_map().count(&child), 1u);
    EXPECT_EQ(echion.weak_task_link_map()[&child].parent, &parent);

    // Only snapshots of the child's own loop count toward expiration.
    echion.prune_weak_task_links(2, { &parent });
    EXPECT_EQ(echion.weak_task_link_map().count(&child), 1u);
    echion.prune_weak_task_links(2, { &parent });
    EXPECT_TRUE(echion.weak_task_link_map().empty());
}

TEST(WeakTaskLinks, OtherLoopSnapshotsDoNotConsumeGracePeriod)
{
    EchionSampler echion;
    PyObject parent{};
    PyObject child{};
    const std::unordered_set<PyObject*> snapshot{ &parent };

    // The child is linked after loop 2 takes its snapshot but before it prunes.
    echion.weak_link_task(&parent, &child, 2);
    echion.prune_weak_task_links(2, snapshot);
    echion.prune_weak_task_links(1, {});
    echion.prune_weak_task_links(1, {});
    ASSERT_EQ(echion.weak_task_link_map().count(&child), 1u);

    echion.prune_weak_task_links(2, { &parent, &child });
    echion.prune_weak_task_links(2, { &parent });
    EXPECT_EQ(echion.weak_task_link_map().count(&child), 1u);
    echion.prune_weak_task_links(2, { &parent });
    EXPECT_TRUE(echion.weak_task_link_map().empty());
}

TEST(WeakTaskLinks, RelinkedAddressBelongsToNewLoop)
{
    EchionSampler echion;
    PyObject parent{};
    PyObject child{};

    echion.weak_link_task(&parent, &child, 1);
    echion.prune_weak_task_links(1, {});
    echion.weak_link_task(&parent, &child, 2);
    echion.prune_weak_task_links(1, {});
    echion.prune_weak_task_links(1, {});
    echion.prune_weak_task_links(2, {});
    EXPECT_EQ(echion.weak_task_link_map().count(&child), 1u);
    echion.prune_weak_task_links(2, {});
    EXPECT_TRUE(echion.weak_task_link_map().empty());
}
