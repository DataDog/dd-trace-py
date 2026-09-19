#include <gtest/gtest.h>

#include <array>
#include <cstdint>

#include "datadog/profiling.hpp"

namespace ddprof = datadog::profiling;

TEST(LibdatadogCxxBridgeTest, PublicProfilingHeaderBuildsAndSerializesDictionaryProfile)
{
    auto dictionary_result = ddprof::ProfileDictionary::create();
    ASSERT_TRUE(dictionary_result->check_and_print());
    auto dictionary = dictionary_result->take_value();

    ddprof::DictionaryStringId function_name{};
    ASSERT_TRUE(dictionary->intern_string("function", function_name));

    ddprof::DictionaryStringId filename{};
    ASSERT_TRUE(dictionary->intern_string("file.py", filename));

    ddprof::DictionaryFunctionId function{};
    ASSERT_TRUE(dictionary->intern_function(
      ddprof::DictionaryFunction{
        function_name,
        {}, // No system name; default string id means empty string.
        filename,
      },
      function));

    rust::Vec<ddprof::SampleType> sample_types;
    sample_types.push_back(ddprof::SampleType::WallTime);
    const ddprof::Period period{ ddprof::SampleType::WallTime, 1 };

    auto profile_result = ddprof::Profile::create_with_dictionary(std::move(sample_types), period, *dictionary);
    ASSERT_TRUE(profile_result->check_and_print());
    auto profile = profile_result->take_value();

    std::array<ddprof::DictionaryLocation, 1> locations{ ddprof::DictionaryLocation{
      {}, // No mapping; default mapping id means unknown mapping.
      function,
      0,
      1,
    } };
    std::array<std::int64_t, 1> values{ 1 };
    std::array<ddprof::DictionaryLabel, 0> labels{};

    ASSERT_TRUE(profile->add_dictionary_sample(ddprof::views::dictionary_sample(locations, values, labels)));

    auto encoded_result = profile->serialize();
    ASSERT_TRUE(encoded_result->check_and_print());
    auto encoded = encoded_result->take_value();
    EXPECT_GT(encoded->bytes().size(), 0);
}
