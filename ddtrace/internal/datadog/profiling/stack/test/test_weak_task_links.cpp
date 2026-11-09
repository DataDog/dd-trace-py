#include "echion/echion_sampler.h"

#include <gtest/gtest.h>

#include <unordered_set>

TEST(WeakTaskLinks, LinkSurvivesOneSnapshotWithoutChild)
{
    // The child was created and linked after the snapshot was taken, so the snapshot does not have it yet.
    EchionSampler echion;
    PyObject parent{};
    PyObject child{};

    echion.weak_link_task(&parent, &child);
    echion.prune_weak_task_links({ &parent });
    ASSERT_EQ(echion.weak_task_link_map().count(&child), 1u);
    EXPECT_EQ(echion.weak_task_link_map()[&child], &parent);

    echion.prune_weak_task_links({ &parent, &child });
    echion.prune_weak_task_links({ &parent });
    EXPECT_EQ(echion.weak_task_link_map().count(&child), 1u);
}

TEST(WeakTaskLinks, LinkIsRemovedAfterTwoSnapshotsWithoutChild)
{
    EchionSampler echion;
    PyObject parent{};
    PyObject child{};

    echion.weak_link_task(&parent, &child);
    echion.prune_weak_task_links({ &parent, &child });
    echion.prune_weak_task_links({ &parent });
    echion.prune_weak_task_links({ &parent });
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

    echion.weak_link_task(&old_parent, &child);
    echion.prune_weak_task_links({ &old_parent, &new_parent });
    echion.weak_link_task(&new_parent, &child);
    echion.prune_weak_task_links({ &old_parent, &new_parent });
    ASSERT_EQ(echion.weak_task_link_map().count(&child), 1u);
    EXPECT_EQ(echion.weak_task_link_map()[&child], &new_parent);
}
