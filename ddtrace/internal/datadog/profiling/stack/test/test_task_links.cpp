#include "echion/echion_sampler.h"

#include <gtest/gtest.h>

#include <unordered_set>

TEST(TaskLinks, LinkSurvivesOneSnapshotWithoutChild)
{
    // The child was created and linked by gather after the snapshot was taken, so the snapshot does not have it yet.
    EchionSampler echion;
    PyObject parent{};
    PyObject child{};

    echion.link_task(&parent, &child);
    echion.prune_task_links({ &parent });
    ASSERT_EQ(echion.task_link_map().count(&child), 1u);
    EXPECT_EQ(echion.task_link_map()[&child], &parent);

    echion.prune_task_links({ &parent, &child });
    echion.prune_task_links({ &parent });
    EXPECT_EQ(echion.task_link_map().count(&child), 1u);
}

TEST(TaskLinks, LinkIsRemovedAfterTwoSnapshotsWithoutChild)
{
    EchionSampler echion;
    PyObject parent{};
    PyObject child{};

    echion.link_task(&parent, &child);
    echion.prune_task_links({ &parent, &child });
    echion.prune_task_links({ &parent });
    echion.prune_task_links({ &parent });
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
        echion.link_task(&parent, &child);
    }
    echion.prune_task_links({ &parent });
    echion.prune_task_links({ &parent });
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

    echion.link_task(&old_parent, &child);
    echion.prune_task_links({ &old_parent, &new_parent });
    echion.link_task(&new_parent, &child);
    echion.prune_task_links({ &old_parent, &new_parent });
    ASSERT_EQ(echion.task_link_map().count(&child), 1u);
    EXPECT_EQ(echion.task_link_map()[&child], &new_parent);
}

TEST(TaskLinks, StrongAndWeakLinksArePrunedIndependently)
{
    // The same child can have both a gather link and a create_task link. Missing it from one snapshot must start the
    // grace period of both links, not let one consume the grace period of the other.
    EchionSampler echion;
    PyObject parent{};
    PyObject child{};

    echion.link_task(&parent, &child);
    echion.weak_link_task(&parent, &child);
    echion.prune_task_links({ &parent });
    echion.prune_weak_task_links({ &parent });
    EXPECT_EQ(echion.task_link_map().count(&child), 1u);
    EXPECT_EQ(echion.weak_task_link_map().count(&child), 1u);

    echion.prune_task_links({ &parent });
    echion.prune_weak_task_links({ &parent });
    EXPECT_EQ(echion.task_link_map().count(&child), 0u);
    EXPECT_EQ(echion.weak_task_link_map().count(&child), 0u);
}
