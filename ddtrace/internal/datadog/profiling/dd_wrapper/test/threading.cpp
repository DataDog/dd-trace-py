#include "test_utils.hpp"
#include "interface.hpp"
#include <gtest/gtest.h>

#include <chrono>

// NOTE: cmake gives us an old gtest, and rather than update I just use the
//       "workaround" in the following link
//       https://stackoverflow.com/a/71257678
void generic_launch_sleep_upload(int n) {
  // NB will fail with more than 4 threads, since we only allow
  // 4 samplers
  std::atomic<bool> done{false};
  std::vector<std::thread> threads;
  std::vector<unsigned int> ids;
  for (int i = 0; i < n; i++)
      ids.push_back(i);
  launch_samplers(ids, threads, done);
  std::this_thread::sleep_for(std::chrono::seconds(1));
  join_samplers(threads, done);
  ddup_upload(); // upload will fail right away, no need to wait
}

void
sample_in_1thread()
{
    configure("my_test_service", "my_test_env", "0.0.1", "https://localhost:8126",
              "cpython", "3.10.6", "3.100", 256);
    generic_launch_sleep_upload(1);
    std::exit(0);
}

TEST(ThreadingDeathTest, SampleIn1Thread)
{
    EXPECT_EXIT(sample_in_1thread(), ::testing::ExitedWithCode(0), "");
}

void
sample_in_2threads()
{
    configure("my_test_service", "my_test_env", "0.0.1", "https://localhost:8126",
              "cpython", "3.10.6", "3.100", 256);
    generic_launch_sleep_upload(2);
    std::exit(0);
}

TEST(ThreadingDeathTest, SampleIn2Threads)
{
    // Currently we have a memory leak here.  It's about 2 pages and it appears to
    // be a fixed cost per thread.
    // TODO #SERIOUS fix this
    EXPECT_EXIT(sample_in_2threads(), ::testing::ExitedWithCode(0), "");
}

void
sample_in_4threads()
{
    configure("my_test_service", "my_test_env", "0.0.1", "https://localhost:8126",
              "cpython", "3.10.6", "3.100", 256);
    generic_launch_sleep_upload(4);
}

TEST(ThreadingDeathTest, SampleIn4Threads)
{
    // This is a test to quickly verify the scaling properties of the leak
    EXPECT_EXIT(sample_in_4threads(), ::testing::ExitedWithCode(0), "");
}

int
main(int argc, char** argv)
{
    ::testing::InitGoogleTest(&argc, argv);
    (void)(::testing::GTEST_FLAG(death_test_style) = "threadsafe");
    return RUN_ALL_TESTS();
}
