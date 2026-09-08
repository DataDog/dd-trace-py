#include <gtest/gtest.h>

#include <array>
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

TEST(LibdatadogCxxBridgeTest, CreateAddTimestampedAndSerializeProfile)
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
          datadog::profiling::Function{ "stage2_timestamped_function", "", "stage2_timestamped_file.py" },
          0,
          12,
        });

        profile->add_sample_with_timestamp(sample, 42);
        auto encoded = profile->serialize_to_vec();

        EXPECT_GT(encoded.size(), 0);
    } catch (const std::exception& err) {
        FAIL() << "libdatadog CXX bridge timestamped call failed: " << err.what();
    }
}

TEST(LibdatadogCxxBridgeTest, CreateAddApi2AndSerializeProfile)
{
    try {
        auto dictionary = datadog::profiling::ProfilesDictionary::create();
        const auto mapping_filename = dictionary->insert_string("/usr/lib/libstage2.so");
        const auto build_id = dictionary->insert_string("stage2-build-id");
        const auto function_name = dictionary->insert_string("stage2_api2_function");
        const auto system_name = dictionary->insert_string("_Z19stage2_api2_functionv");
        const auto file_name = dictionary->insert_string("stage2_api2_file.py");
        const auto label_key = dictionary->insert_string("pid");

        const auto mapping = dictionary->insert_mapping(datadog::profiling::Mapping2{
          0x10000000,
          0x20000000,
          0,
          mapping_filename,
          build_id,
        });
        const auto function = dictionary->insert_function(datadog::profiling::Function2{
          function_name,
          system_name,
          file_name,
        });

        rust::Vec<datadog::profiling::SampleType> sample_types;
        sample_types.push_back(datadog::profiling::SampleType::WallTime);
        const datadog::profiling::Period period{ datadog::profiling::SampleType::WallTime, 1 };
        auto profile =
          datadog::profiling::Profile::create_with_dictionary(std::move(sample_types), period, *dictionary);

        std::array<datadog::profiling::Location2, 1> locations{ datadog::profiling::Location2{
          mapping,
          function,
          0x10003000,
          12,
        } };
        std::array<std::int64_t, 1> values{ 1'000'000 };
        std::array<datadog::profiling::Label2, 1> labels{ datadog::profiling::Label2{
          label_key,
          "",
          101,
          "",
        } };
        const datadog::profiling::Sample2 sample{
            { locations.data(), locations.size() },
            { values.data(), values.size() },
            { labels.data(), labels.size() },
        };

        profile->add_sample2(sample, 42);
        profile->add_sample2(sample, 0);
        auto encoded = profile->serialize_to_vec();

        EXPECT_GT(encoded.size(), 0);
    } catch (const std::exception& err) {
        FAIL() << "libdatadog CXX bridge api2 call failed: " << err.what();
    }
}

TEST(LibdatadogCxxBridgeTest, RejectsApi2OnProfileWithoutDictionary)
{
    auto dictionary = datadog::profiling::ProfilesDictionary::create();
    const auto mapping_filename = dictionary->insert_string("/usr/lib/libstage2.so");
    const auto build_id = dictionary->insert_string("stage2-build-id");
    const auto function_name = dictionary->insert_string("stage2_api2_function");
    const auto system_name = dictionary->insert_string("_Z19stage2_api2_functionv");
    const auto file_name = dictionary->insert_string("stage2_api2_file.py");

    const auto mapping = dictionary->insert_mapping(datadog::profiling::Mapping2{
      0x10000000,
      0x20000000,
      0,
      mapping_filename,
      build_id,
    });
    const auto function = dictionary->insert_function(datadog::profiling::Function2{
      function_name,
      system_name,
      file_name,
    });

    rust::Vec<datadog::profiling::SampleType> sample_types;
    sample_types.push_back(datadog::profiling::SampleType::WallTime);
    const datadog::profiling::Period period{ datadog::profiling::SampleType::WallTime, 1 };
    auto profile = datadog::profiling::Profile::create(std::move(sample_types), period);

    std::array<datadog::profiling::Location2, 1> locations{ datadog::profiling::Location2{
      mapping,
      function,
      0x10003000,
      12,
    } };
    std::array<std::int64_t, 1> values{ 1'000'000 };
    const datadog::profiling::Sample2 sample{
        { locations.data(), locations.size() },
        { values.data(), values.size() },
        {},
    };

    EXPECT_THROW(profile->add_sample2(sample, 42), std::exception);
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

TEST(LibdatadogCxxBridgeTest, RejectsZeroTimestamp)
{
    rust::Vec<datadog::profiling::SampleType> sample_types;
    sample_types.push_back(datadog::profiling::SampleType::WallTime);
    const datadog::profiling::Period period{ datadog::profiling::SampleType::WallTime, 1 };
    auto profile = datadog::profiling::Profile::create(std::move(sample_types), period);

    datadog::profiling::Sample sample;
    sample.values.push_back(1'000'000);
    sample.locations.push_back(datadog::profiling::Location{
      datadog::profiling::Mapping{ 0, 0, 0, "", "" },
      datadog::profiling::Function{ "stage2_timestamped_function", "", "stage2_timestamped_file.py" },
      0,
      12,
    });

    EXPECT_THROW(profile->add_sample_with_timestamp(sample, 0), std::exception);
}
