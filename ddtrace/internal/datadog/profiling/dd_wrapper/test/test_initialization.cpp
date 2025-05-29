#include "ddup_interface.hpp"
#include "test_utils.hpp"
#include <gtest/gtest.h>

// NOTE: cmake gives us an old gtest, and rather than update I just use the
//       "workaround" in the following link
//       https://stackoverflow.com/a/71257678

void
simple_init()
{
    configure(256);
    std::exit(0);
}

TEST(InitDeathTest, TestInit)
{
    EXPECT_EXIT(simple_init(), ::testing::ExitedWithCode(0), "");
}

void
empty_init()
{
    configure(0);
    std::exit(0);
}

TEST(DD_WrapperTest, TestInitWithEmpty)
{
    EXPECT_EXIT(empty_init(), ::testing::ExitedWithCode(0), "");
}

void
minimal_init()
{
    std::exit(0);
}

TEST(DD_WrapperTest, TestInitWithMinimal)
{
    EXPECT_EXIT(minimal_init(), ::testing::ExitedWithCode(0), "");
}

void
short_lifetime_init()
{
    ddup_start();
    std::exit(0);
}

TEST(DD_WrapperTest, TestInitWithShortLifetime)
{
    EXPECT_EXIT(short_lifetime_init(), ::testing::ExitedWithCode(0), "");
}

int
main(int argc, char** argv)
{
    ::testing::InitGoogleTest(&argc, argv);
    (void)(::testing::GTEST_FLAG(death_test_style) = "threadsafe");
    return RUN_ALL_TESTS();
}
