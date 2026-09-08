#include <gtest/gtest.h>

#include <libdd-profiling/src/cxx.rs.h>

TEST(LibdatadogCxxBridgeTest, ProfilingTypesCompile)
{
    datadog::profiling::Period period{ datadog::profiling::SampleType::WallTime, 1 };
    EXPECT_EQ(period.value_type, datadog::profiling::SampleType::WallTime);
    EXPECT_EQ(period.value, 1);

    rust::Vec<datadog::profiling::SampleType> sample_types;
    sample_types.push_back(datadog::profiling::SampleType::WallTime);
    EXPECT_EQ(sample_types.size(), 1);
    EXPECT_EQ(sample_types[0], datadog::profiling::SampleType::WallTime);
}
