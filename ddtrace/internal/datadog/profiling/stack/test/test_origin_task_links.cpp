#include "origin_task_links.hpp"

#include <gtest/gtest.h>

#include <optional>
#include <string>

using Datadog::OriginTask;
using Datadog::OriginTaskLinks;

class OriginTaskLinksTest : public ::testing::Test
{
  protected:
    void SetUp() override { OriginTaskLinks::get_instance().disable_and_reset(); }

    void TearDown() override { OriginTaskLinks::get_instance().disable_and_reset(); }
};

TEST_F(OriginTaskLinksTest, DisabledByDefault)
{
    auto& links = OriginTaskLinks::get_instance();
    EXPECT_FALSE(links.is_enabled());
    links.link_origin_task(1, 100, "task");
    EXPECT_EQ(links.get_origin_task(1), std::nullopt);
}

TEST_F(OriginTaskLinksTest, EnableAllowsLinkAndGet)
{
    auto& links = OriginTaskLinks::get_instance();
    links.enable();
    ASSERT_TRUE(links.is_enabled());

    links.link_origin_task(1, 100, "worker-task");
    auto got = links.get_origin_task(1);
    ASSERT_TRUE(got.has_value());
    EXPECT_EQ(got->task_id, 100u);
    EXPECT_EQ(got->task_name, "worker-task");

    links.unlink_origin_task(1);
    EXPECT_EQ(links.get_origin_task(1), std::nullopt);
}

TEST_F(OriginTaskLinksTest, DisableAndResetClearsMap)
{
    auto& links = OriginTaskLinks::get_instance();
    links.enable();
    links.link_origin_task(1, 100, "task");
    ASSERT_TRUE(links.get_origin_task(1).has_value());

    links.disable_and_reset();
    EXPECT_FALSE(links.is_enabled());
    EXPECT_EQ(links.get_origin_task(1), std::nullopt);
}

TEST_F(OriginTaskLinksTest, StaleLinkAfterDisableIsIgnored)
{
    auto& links = OriginTaskLinks::get_instance();
    links.enable();
    links.link_origin_task(1, 100, "before");
    links.disable_and_reset();

    // Simulates a worker that observed enabled before Profiler.stop() and
    // attempts to link after disable_and_reset() cleared the map.
    links.link_origin_task(1, 200, "stale");
    EXPECT_FALSE(links.is_enabled());
    EXPECT_EQ(links.get_origin_task(1), std::nullopt);
}
