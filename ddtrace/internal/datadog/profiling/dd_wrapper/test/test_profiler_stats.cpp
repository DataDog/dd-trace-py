#include "profiler_stats.hpp"

#include <gtest/gtest.h>

#include <string>

namespace {

std::string
intern_failed_field(size_t count)
{
    return "\"intern_failed_count\": " + std::to_string(count) + ",";
}

} // namespace

// UploaderBuilder::build swaps the stats, then adds the pending count onto the
// object it swapped out. Rendering metadata later must not move skips that
// happened after that snapshot onto this profile, or onto the next one.
TEST(ProfilerStatsTest, SnapshotKeepsInternFailedCount)
{
    Datadog::ProfilerStats discard;
    discard.reset_state();

    Datadog::ProfilerStats::incr_intern_failed_count();
    Datadog::ProfilerStats::incr_intern_failed_count();
    Datadog::ProfilerStats::incr_intern_failed_count();

    Datadog::ProfilerStats snapshot;
    snapshot.add_pending_intern_failed_count();

    Datadog::ProfilerStats::incr_intern_failed_count();
    Datadog::ProfilerStats::incr_intern_failed_count();

    const std::string metadata = snapshot.get_internal_metadata_json();
    EXPECT_NE(metadata.find(intern_failed_field(3)), std::string::npos) << metadata;
    EXPECT_EQ(snapshot.get_intern_failed_count(), 3u);

    const std::string rendered_again = snapshot.get_internal_metadata_json();
    EXPECT_NE(rendered_again.find(intern_failed_field(3)), std::string::npos) << rendered_again;
    EXPECT_EQ(snapshot.get_intern_failed_count(), 3u);

    Datadog::ProfilerStats next_profile;
    const std::string next_before_snapshot = next_profile.get_internal_metadata_json();
    EXPECT_NE(next_before_snapshot.find(intern_failed_field(0)), std::string::npos) << next_before_snapshot;
    EXPECT_EQ(next_profile.get_intern_failed_count(), 0u);

    next_profile.add_pending_intern_failed_count();
    const std::string next_metadata = next_profile.get_internal_metadata_json();
    EXPECT_NE(next_metadata.find(intern_failed_field(2)), std::string::npos) << next_metadata;
    EXPECT_EQ(snapshot.get_intern_failed_count(), 3u);
    EXPECT_EQ(next_profile.get_intern_failed_count(), 2u);
}
