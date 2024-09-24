#pragma once

#include <atomic>
#include <memory>
#include <mutex>
#include <string>
#include <string_view>
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
  public:
    // Public static method to access the CodeProvenance instance
    static CodeProvenance& get_instance()
    {
        static CodeProvenance instance;
        return instance;
    }

    // Delete copy constructor and assignment operator to prevent copies
    CodeProvenance(CodeProvenance const&) = delete;
    CodeProvenance& operator=(CodeProvenance const&) = delete;

    void postfork_child();

    void set_enabled(bool enable);
    bool enabled() const;
    void add_filename(std::string_view filename);
    std::string serialize_to_json_str();

  private:
    // Mutex to protect the state
    std::mutex mtx;
    // Whether this is enabled, set only when DD_PROFILING_ENABLE_CODE_PROVENANCE is set
    bool enabled = false;
    // Mapping from package name to Package object
    std::unordered_map<std::string, std::unique_ptr<Package>> packages;
    // Mapping from Package object to list of filenames that are associated with the package
    std::unordered_map<const Package*, std::vector<std::string>> packages_to_files;

    // Private Constructor/Destructor to prevent instantiation/deletion from outside
    CodeProvenance() = default;
    ~CodeProvenance() = default;

    void reset();
    std::string get_package_name(std::string_view filename);
    std::string get_site_packages_path(std::string_view filename);
    std::string get_package_version(std::string_view filename, std::string_view package_name);
    const Package* get_package(std::string_view filename);
    const Package* add_new_package(std::string package_name, std::string version);
};
}
