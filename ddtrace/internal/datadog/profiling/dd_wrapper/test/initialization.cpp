#include "ddup_interface.hpp"
#include "test_utils.hpp"
#include <gtest/gtest.h>

// NOTE: cmake gives us an old gtest, and rather than update I just use the
//       "workaround" in the following link
//       https://stackoverflow.com/a/71257678

void
simple_init()
{
    configure("my_test_service", "my_test_env", "0.0.1", "https://localhost:8126", "cpython", "3.10.6", "3.100", 256);
    std::exit(0);
}

TEST(InitDeathTest, TestInit)
{
    EXPECT_EXIT(simple_init(), ::testing::ExitedWithCode(0), "");
}

void
empty_init()
{
    configure("", "", "", "", "", "", "", 0);
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
    // This will probably work under normal circumstances, since there may not be extreme damage
    // to the stack between the two operations.  However, if there are defects they should be
    // evident under sanitizers.
    {
        std::string service("my_test_service");
        std::string env("my_test_env");
        std::string version("0.0.1");
        std::string url("https://localhost:8126");
        std::string runtime("cpython");
        std::string runtime_version("3.10.6");
        std::string profiler_version("3.100");

        ddup_config_service(service.c_str());
        ddup_config_env(env.c_str());
        ddup_config_version(version.c_str());
        ddup_config_url(url.c_str());
        ddup_config_runtime(runtime.c_str());
        ddup_config_runtime_version(runtime_version.c_str());
        ddup_config_profiler_version(profiler_version.c_str());
    }

    ddup_init();
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
