#include <taint_tracking/taint_range.h>
#include <tests/test_common.hpp>

using TaintRangeOriginTest = ::testing::Test;

// Tests for has_origin method
TEST(TaintRange, CheckSingleSource)
{
    // Create a taint range with a COOKIE source
    Source source("name", "value", OriginType::COOKIE);
    TaintRange taint_range(0, 2, source);

    // Check that the source matches
    EXPECT_TRUE(taint_range.has_origin(OriginType::COOKIE));
    // Check that other sources don't match
    EXPECT_FALSE(taint_range.has_origin(OriginType::PARAMETER));
}

TEST(TaintRange, CheckAllSourceTypes)
{
    // Test all source types
    std::vector<OriginType> origins = { OriginType::PARAMETER_NAME, OriginType::PARAMETER, OriginType::HEADER_NAME,
                                        OriginType::COOKIE,         OriginType::BODY,      OriginType::PATH };

    for (const auto& origin : origins) {
        Source source("name", "value", origin);
        TaintRange taint_range(0, 2, source);

        // Check that the correct source matches
        EXPECT_TRUE(taint_range.has_origin(origin));

        // Check that other sources don't match
        for (const auto& other_origin : origins) {
            if (other_origin != origin) {
                EXPECT_FALSE(taint_range.has_origin(other_origin));
            }
        }
    }
}

TEST(TaintRange, SourcePersistsAfterReset)
{
    // Create a taint range with a source and some secure marks
    Source source("name", "value", OriginType::COOKIE);
    TaintRange taint_range(0, 2, source);

    // Verify initial state
    EXPECT_TRUE(taint_range.has_origin(OriginType::COOKIE));

    // Reset the taint range
    taint_range.reset();

    // Verify that secure marks are cleared but source remains
    EXPECT_FALSE(taint_range.has_origin(OriginType::COOKIE));
    EXPECT_TRUE(taint_range.has_origin(OriginType::EMPTY));
}

TEST(TaintRange, SourceCopyConstructor)
{
    // Create a taint range with a source
    Source source("name", "value", OriginType::COOKIE);
    Source source2("name", "value", OriginType::HEADER_NAME);
    TaintRange original(0, 2, source);

    // Create a copy
    TaintRange copy = original;

    // Verify both have the same source
    EXPECT_TRUE(original.has_origin(OriginType::COOKIE));
    EXPECT_TRUE(copy.has_origin(OriginType::COOKIE));

    original.source = source2;

    // Verify both haven't the same source
    EXPECT_TRUE(original.has_origin(OriginType::HEADER_NAME));
    EXPECT_TRUE(copy.has_origin(OriginType::COOKIE));
}
