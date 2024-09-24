#pragma once

#include <atomic>
#include <memory>
#include <string>
#include <string_view>
#include <mutex>
#include <unordered_map>
#include <vector>

namespace Datadog {

struct Package
{
    std::string name;
    std::string kind;
    std::string version;
};

static constexpr const char* STDLIB = "stdlib";

class CodeProvenance
{
  private:
    std::atomic<bool> first_time{ true };
    std::mutex mtx{};

    bool enabled = false;
    // Set of packages that have been seen
    std::unordered_map<std::string, std::unique_ptr<Package>> packages;

    std::unordered_map<const Package*, std::vector<std::string>> packages_to_files;

    std::string get_package_name(std::string_view filename);
    std::string get_site_packages_path(std::string_view filename);
    std::string get_package_version(std::string_view filename, std::string_view package_name);
    const Package* get_package(std::string_view filename);
    const Package* add_new_package(std::string package_name, std::string version);

  public:
    CodeProvenance() {};
    void one_time_init(bool enable);
    void postfork_child();

    void insert_filename(std::string_view filename);
    std::string serialize_to_str();
};
}
