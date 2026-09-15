#include <gtest/gtest.h>

#include <array>
#include <cstdint>
#include <stdexcept>
#include <string>
#include <utility>

#include "datadog/profiling.hpp"

namespace ddprof = datadog::profiling;

namespace {

rust::Vec<ddprof::SampleType>
wall_time_sample_types()
{
    rust::Vec<ddprof::SampleType> sample_types;
    sample_types.push_back(ddprof::SampleType::WallTime);
    return sample_types;
}

rust::Box<ddprof::Profile>
create_profile()
{
    const ddprof::Period period{ ddprof::SampleType::WallTime, 1 };
    auto result = ddprof::Profile::create(wall_time_sample_types(), period);
    if (!result->ok()) {
        throw std::runtime_error(std::string(result->message()));
    }
    return result->take_value();
}

rust::Box<ddprof::ProfileDictionary>
create_dictionary()
{
    auto result = ddprof::ProfileDictionary::create();
    if (!result->ok()) {
        throw std::runtime_error(std::string(result->message()));
    }
    return result->take_value();
}

rust::Box<ddprof::Profile>
create_dictionary_profile(const ddprof::ProfileDictionary& dictionary)
{
    const ddprof::Period period{ ddprof::SampleType::WallTime, 1 };
    auto result = ddprof::Profile::create_with_dictionary(wall_time_sample_types(), period, dictionary);
    if (!result->ok()) {
        throw std::runtime_error(std::string(result->message()));
    }
    return result->take_value();
}

std::string
take_error_message(ddprof::Profile& profile)
{
    auto errors = profile.take_errors();
    if (errors.empty()) {
        return "no error details";
    }
    return std::string(errors[0].operation_name()) + ": " + std::string(errors[0].message);
}

std::string
take_error_message(const ddprof::ProfileDictionary& dictionary)
{
    auto errors = dictionary.take_errors();
    if (errors.empty()) {
        return "no error details";
    }
    return std::string(errors[0].operation_name()) + ": " + std::string(errors[0].message);
}

ddprof::DictionaryStringId
intern_string(const ddprof::ProfileDictionary& dictionary, const char* value)
{
    ddprof::DictionaryStringId id{};
    if (!dictionary.intern_string(value, id)) {
        throw std::runtime_error(take_error_message(dictionary));
    }
    return id;
}

ddprof::DictionaryMappingId
intern_mapping(const ddprof::ProfileDictionary& dictionary,
               ddprof::DictionaryStringId filename,
               ddprof::DictionaryStringId build_id)
{
    ddprof::DictionaryMappingId id{};
    if (!dictionary.intern_mapping(ddprof::DictionaryMapping{ 0x10000000, 0x20000000, 0, filename, build_id }, id)) {
        throw std::runtime_error(take_error_message(dictionary));
    }
    return id;
}

ddprof::DictionaryFunctionId
intern_function(const ddprof::ProfileDictionary& dictionary,
                ddprof::DictionaryStringId name,
                ddprof::DictionaryStringId system_name,
                ddprof::DictionaryStringId filename)
{
    ddprof::DictionaryFunctionId id{};
    if (!dictionary.intern_function(ddprof::DictionaryFunction{ name, system_name, filename }, id)) {
        throw std::runtime_error(take_error_message(dictionary));
    }
    return id;
}

rust::Box<ddprof::EncodedProfile>
serialize(ddprof::Profile& profile)
{
    auto result = profile.serialize();
    if (!result->ok()) {
        throw std::runtime_error(std::string(result->message()));
    }
    return result->take_value();
}

} // namespace

TEST(LibdatadogCxxBridgeTest, ProfilingTypesCompile)
{
    ddprof::Period period{ ddprof::SampleType::WallTime, 1 };
    EXPECT_EQ(period.value_type, ddprof::SampleType::WallTime);
    EXPECT_EQ(period.value, 1);

    auto sample_types = wall_time_sample_types();
    EXPECT_EQ(sample_types.size(), 1);
    EXPECT_EQ(sample_types[0], ddprof::SampleType::WallTime);
}

TEST(LibdatadogCxxBridgeTest, CreateAddAndSerializeProfile)
{
    auto profile = create_profile();
    std::array<ddprof::Location, 1> locations{ ddprof::Location{
      ddprof::Mapping{ 0, 0, 0, "", "" },
      ddprof::Function{ "stage2_function", "", "stage2_file.py" },
      0,
      12,
    } };
    std::array<std::int64_t, 1> values{ 1'000'000 };
    std::array<ddprof::Label, 0> labels{};

    ASSERT_TRUE(profile->add_sample(ddprof::views::sample(locations, values, labels)));
    EXPECT_GT(serialize(*profile)->bytes().size(), 0);
}

TEST(LibdatadogCxxBridgeTest, CreateAddTimestampedAndSerializeProfile)
{
    auto profile = create_profile();
    std::array<ddprof::Location, 1> locations{ ddprof::Location{
      ddprof::Mapping{ 0, 0, 0, "", "" },
      ddprof::Function{ "stage2_timestamped_function", "", "stage2_timestamped_file.py" },
      0,
      12,
    } };
    std::array<std::int64_t, 1> values{ 1'000'000 };
    std::array<ddprof::Label, 0> labels{};

    ASSERT_TRUE(profile->add_sample(ddprof::views::sample(locations, values, labels), 42));
    EXPECT_GT(serialize(*profile)->bytes().size(), 0);
}

TEST(LibdatadogCxxBridgeTest, CreateAddDictionaryAndSerializeProfile)
{
    auto dictionary = create_dictionary();
    const auto mapping_filename = intern_string(*dictionary, "/usr/lib/libstage2.so");
    const auto build_id = intern_string(*dictionary, "stage2-build-id");
    const auto function_name = intern_string(*dictionary, "stage2_dictionary_function");
    const auto system_name = intern_string(*dictionary, "_Z26stage2_dictionary_functionv");
    const auto file_name = intern_string(*dictionary, "stage2_dictionary_file.py");
    const auto label_key = intern_string(*dictionary, "pid");
    const auto mapping = intern_mapping(*dictionary, mapping_filename, build_id);
    const auto function = intern_function(*dictionary, function_name, system_name, file_name);

    auto profile = create_dictionary_profile(*dictionary);
    std::array<ddprof::DictionaryLocation, 1> locations{ ddprof::DictionaryLocation{
      mapping,
      function,
      0x10003000,
      12,
    } };
    std::array<std::int64_t, 1> values{ 1'000'000 };
    std::array<ddprof::DictionaryLabel, 1> labels{ ddprof::DictionaryLabel{ label_key, "", 101, "" } };
    const auto sample = ddprof::views::dictionary_sample(locations, values, labels);

    ASSERT_TRUE(profile->add_dictionary_sample(sample, 42));
    ASSERT_TRUE(profile->add_dictionary_sample(sample));
    EXPECT_GT(serialize(*profile)->bytes().size(), 0);
}

TEST(LibdatadogCxxBridgeTest, SendEncodedProfileApiCompilesAndReportsError)
{
    auto profile = create_profile();
    std::array<ddprof::Location, 1> locations{ ddprof::Location{
      ddprof::Mapping{ 0, 0, 0, "", "" },
      ddprof::Function{ "send_encoded_profile_function", "", "send_encoded_profile_file.py" },
      0,
      12,
    } };
    std::array<std::int64_t, 1> values{ 1'000'000 };
    std::array<ddprof::Label, 0> labels{};
    ASSERT_TRUE(profile->add_sample(ddprof::views::sample(locations, values, labels)));
    auto encoded = serialize(*profile);

    rust::Vec<ddprof::Tag> tags;
    tags.push_back(ddprof::Tag{ "language", "python" });
    auto exporter_result = ddprof::ProfileExporter::create_agent_exporter(
      "dd-trace-py", "test", "python", std::move(tags), "http://127.0.0.1:1", 1, false);
    ASSERT_TRUE(exporter_result->check_and_print());
    auto exporter = exporter_result->take_value();

    const auto status = exporter->send_encoded_profile(std::move(encoded), {}, {}, "", "{}", "");
    EXPECT_FALSE(status.ok());
}

TEST(LibdatadogCxxBridgeTest, RejectsDictionarySampleOnProfileWithoutDictionary)
{
    auto dictionary = create_dictionary();
    const auto mapping_filename = intern_string(*dictionary, "/usr/lib/libstage2.so");
    const auto build_id = intern_string(*dictionary, "stage2-build-id");
    const auto function_name = intern_string(*dictionary, "stage2_dictionary_function");
    const auto system_name = intern_string(*dictionary, "_Z26stage2_dictionary_functionv");
    const auto file_name = intern_string(*dictionary, "stage2_dictionary_file.py");
    const auto mapping = intern_mapping(*dictionary, mapping_filename, build_id);
    const auto function = intern_function(*dictionary, function_name, system_name, file_name);

    auto profile = create_profile();
    std::array<ddprof::DictionaryLocation, 1> locations{ ddprof::DictionaryLocation{
      mapping,
      function,
      0x10003000,
      12,
    } };
    std::array<std::int64_t, 1> values{ 1'000'000 };
    std::array<ddprof::DictionaryLabel, 0> labels{};
    const auto sample = ddprof::views::dictionary_sample(locations, values, labels);

    EXPECT_FALSE(profile->add_dictionary_sample(sample, 42));
    EXPECT_NE(take_error_message(*profile).find("profiles dictionary not set"), std::string::npos);
}

TEST(LibdatadogCxxBridgeTest, RejectsInvalidSampleValueCount)
{
    auto profile = create_profile();
    std::array<ddprof::Location, 0> locations{};
    std::array<std::int64_t, 0> values{};
    std::array<ddprof::Label, 0> labels{};

    EXPECT_FALSE(profile->add_sample(ddprof::views::sample(locations, values, labels)));
    EXPECT_FALSE(profile->take_errors().empty());
}

TEST(LibdatadogCxxBridgeTest, RejectsZeroTimestamp)
{
    auto profile = create_profile();
    std::array<ddprof::Location, 1> locations{ ddprof::Location{
      ddprof::Mapping{ 0, 0, 0, "", "" },
      ddprof::Function{ "stage2_timestamped_function", "", "stage2_timestamped_file.py" },
      0,
      12,
    } };
    std::array<std::int64_t, 1> values{ 1'000'000 };
    std::array<ddprof::Label, 0> labels{};

    EXPECT_FALSE(profile->add_sample(ddprof::views::sample(locations, values, labels), 0));
    EXPECT_NE(take_error_message(*profile).find("endtime_ns must be non-zero"), std::string::npos);
}
