#include "code_provenance.hpp"

#include <gmock/gmock.h>
#include <gtest/gtest.h>
#include <nlohmann/json.hpp>

#include <unordered_map>

namespace Datadog {

using json = nlohmann::json;
using ::testing::UnorderedElementsAreArray;

class CodeProvenanceTest : public ::testing::Test
{
  protected:
    CodeProvenance& cp = CodeProvenance::get_instance();
    void SetUp() override
    {
        cp.set_enabled(true);
        cp.set_runtime_version("3.10.6");
        cp.set_stdlib_path("/usr/lib/python3.10");

        std::unordered_map<std::string_view, std::pair<std::string_view, std::string_view>> packages = {
            { "requests", std::make_pair("2.26.0", "/usr/lib/python3.10/site-packages/requests") },
            { "urllib3", std::make_pair("1.26.7", "/usr/lib/python3.10/site-packages/urllib3") },
            { "chardet", std::make_pair("4.0.0", "/usr/lib/python3.10/site-packages/chardet") },
            { "idna", std::make_pair("3.2", "/usr/lib/python3.10/site-packages/idna") },
            { "certifi", std::make_pair("2021.5.30", "/usr/lib/python3.10/site-packages/certifi") },
        };

        cp.add_packages(packages);
    }

    void TearDown() override {}
};

TEST_F(CodeProvenanceTest, SingletonInstance)
{
    CodeProvenance& cp2 = CodeProvenance::get_instance();
    ASSERT_EQ(&cp, &cp2);
}

TEST_F(CodeProvenanceTest, SerializeJsonStr)
{

    cp.add_filename("/usr/lib/python3.10/site-packages/requests/__init__.py");
    cp.add_filename("/usr/lib/python3.10/site-packages/urllib3/__init__.py");
    cp.add_filename("/usr/lib/python3.10/site-packages/chardet/chardet.py");
    cp.add_filename("/usr/lib/python3.10/site-packages/idna/util.py");
    cp.add_filename("/usr/lib/python3.10/site-packages/certifi/cert.py");
    cp.add_filename("/usr/lib/python3.10/site-packages/certifi/__init__.py");

    std::optional<std::string> json_str = cp.try_serialize_to_json_str();
    ASSERT_TRUE(json_str.has_value());
    json parsed_json = json::parse(json_str.value());

    ASSERT_TRUE(parsed_json.contains("v1"));
    ASSERT_TRUE(parsed_json["v1"].is_array());
    EXPECT_THAT(parsed_json["v1"],
                UnorderedElementsAreArray({ json({
                                              { "name", "requests" },
                                              { "kind", "library" },
                                              { "version", "2.26.0" },
                                              { "paths", { "/usr/lib/python3.10/site-packages/requests" } },
                                            }),
                                            json({
                                              { "name", "urllib3" },
                                              { "kind", "library" },
                                              { "version", "1.26.7" },
                                              { "paths", { "/usr/lib/python3.10/site-packages/urllib3" } },
                                            }),
                                            json({
                                              { "name", "chardet" },
                                              { "kind", "library" },
                                              { "version", "4.0.0" },
                                              { "paths", { "/usr/lib/python3.10/site-packages/chardet" } },
                                            }),
                                            json({
                                              { "name", "idna" },
                                              { "kind", "library" },
                                              { "version", "3.2" },
                                              { "paths", { "/usr/lib/python3.10/site-packages/idna" } },
                                            }),
                                            json({
                                              { "name", "certifi" },
                                              { "kind", "library" },
                                              { "version", "2021.5.30" },
                                              { "paths",
                                                {
                                                  "/usr/lib/python3.10/site-packages/certifi",
                                                } },
                                            }),
                                            json({ { "name", "stdlib" },
                                                   { "kind", "standard library" },
                                                   { "version", "3.10.6" },
                                                   { "paths", { "/usr/lib/python3.10" } } }) }));

    json_str = cp.try_serialize_to_json_str();
    parsed_json = json::parse(json_str.value());
    ASSERT_TRUE(parsed_json.contains("v1"));
    ASSERT_TRUE(parsed_json["v1"].is_array());
    EXPECT_EQ(parsed_json["v1"].size(), 1);
    EXPECT_EQ(parsed_json["v1"][0],
              json({
                { "name", "stdlib" },
                { "kind", "standard library" },
                { "version", "3.10.6" },
                { "paths", { "/usr/lib/python3.10" } },
              }));
}

} // namespace Datadog
