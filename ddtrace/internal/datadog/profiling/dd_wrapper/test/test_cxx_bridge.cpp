#include <gtest/gtest.h>

#include <cstdint>
#include <exception>
#include <utility>

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

TEST(LibdatadogCxxBridgeTest, CreateAddAndSerializeProfile)
{
    try {
        rust::Vec<datadog::profiling::SampleType> sample_types;
        sample_types.push_back(datadog::profiling::SampleType::WallTime);
        const datadog::profiling::Period period{ datadog::profiling::SampleType::WallTime, 1 };
        auto profile = datadog::profiling::Profile::create(std::move(sample_types), period);

        datadog::profiling::Sample sample;
        sample.values.push_back(1'000'000);
        sample.locations.push_back(datadog::profiling::Location{
          datadog::profiling::Mapping{ 0, 0, 0, "", "" },
          datadog::profiling::Function{ "stage2_function", "", "stage2_file.py" },
          0,
          12,
        });

        profile->add_sample(sample);
        auto encoded = profile->serialize_to_vec();

        EXPECT_GT(encoded.size(), 0);
    } catch (const std::exception& err) {
        FAIL() << "libdatadog CXX bridge call failed: " << err.what();
    }
}

TEST(LibdatadogCxxBridgeTest, RejectsInvalidSampleValueCount)
{
    rust::Vec<datadog::profiling::SampleType> sample_types;
    sample_types.push_back(datadog::profiling::SampleType::WallTime);
    const datadog::profiling::Period period{ datadog::profiling::SampleType::WallTime, 1 };
    auto profile = datadog::profiling::Profile::create(std::move(sample_types), period);

    datadog::profiling::Sample sample;
    EXPECT_THROW(profile->add_sample(sample), std::exception);
}
