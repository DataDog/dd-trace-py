#pragma once

#include <atomic>
#include <memory>
#include <mutex>
#include <optional>
#include <set>
#include <string>
#include <string_view>
#include <unordered_map>
#include <vector>

namespace Datadog {

struct Package
{
    std::string name;
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

    static void postfork_child();

    void set_enabled(bool enable);
    bool is_enabled();
    void set_runtime_version(std::string_view runtime_version);
    void set_stdlib_path(std::string_view stdlib_path);
    void add_packages(std::unordered_map<std::string_view, std::string_view> packages);
    void add_filename(std::string_view filename);
    std::optional<std::string> try_serialize_to_json_str();

  private:
    // Mutex to protect the state
    std::mutex mtx;
    // Whether this is enabled, set only when DD_PROFILING_ENABLE_CODE_PROVENANCE is set
    std::atomic_bool enabled{ false };
    std::string runtime_version;
    std::string stdlib_path;
    // Mapping from package name to Package object
    std::unordered_map<std::string_view, std::unique_ptr<Package>> packages;
    // Mapping from Package object to list of filenames that are associated with the package
    std::unordered_map<const Package*, std::set<std::string>> packages_to_files;

    // Private Constructor/Destructor to prevent instantiation/deletion from outside
    CodeProvenance() = default;
    ~CodeProvenance() = default;

    void reset();
    std::string_view get_package_name(std::string_view filename);
    const Package* add_new_package(std::string_view package_name, std::string_view version);
};
}
