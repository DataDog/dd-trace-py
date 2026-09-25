#include "libdatadog_helpers.hpp"
#include "test_utils.hpp"

#include <gtest/gtest.h>

#include <optional>
#include <string>

using namespace Datadog;

// ============================================================================
// intern_string with invalid UTF-8 (integration, requires profiler init)
// ============================================================================

TEST(InternStringLossy, InvalidUtf8DoesNotThrow)
{
    configure("test", "test", "0.1", "http://localhost:8126", "python", "3.12", "1.0.0", 64);

    std::optional<string_id> result;
    EXPECT_NO_THROW(result = intern_string("\xFF"));
    EXPECT_TRUE(result.has_value());
}

TEST(InternStringLossy, InvalidUtf8InternsDeterministically)
{
    configure("test", "test", "0.1", "http://localhost:8126", "python", "3.12", "1.0.0", 64);

    auto id1 = intern_string("hello\xFFworld");
    auto id2 = intern_string("hello\xFFworld");
    ASSERT_TRUE(id1.has_value());
    ASSERT_TRUE(id2.has_value());
    EXPECT_EQ(id1->handle, id2->handle);
}
