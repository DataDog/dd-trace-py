#include "code_provenance.hpp"

#include <filesystem>
#include <memory>
#include <mutex>
#include <regex>
#include <string>
#include <string_view>
#include <unordered_map>
#include <vector>

namespace Datadog {

void
Datadog::CodeProvenance::postfork_child()
{
    mtx.unlock();
    reset();
}

void
Datadog::CodeProvenance::set_enabled(bool enable)
{
    std::lock_guard<std::mutex> lock(mtx);
    enabled = enable;
}

bool
Datadog::CodeProvenance::is_enabled()
{
    std::lock_guard<std::mutex> lock(mtx);
    return enabled;
}

void
CodeProvenance::add_filename(std::string_view filename)
{
    std::lock_guard<std::mutex> lock(mtx);

    if (!enabled) {
        return;
    }

    std::string package_name = get_package_name(filename);
    if (package_name == STDLIB) {
        return;
    }

    auto it = packages.find(package_name);
    const Package* package = nullptr;

    if (it == packages.end()) {
        // Create a new package
        std::string package_version = get_package_version(filename, package_name);
        package = add_new_package(package_name, package_version);
    } else {
        package = it->second.get();
    }

    if (package) {
        if (packages_to_files.find(package) == packages_to_files.end()) {
            packages_to_files[package] = std::vector<std::string>();
        }
        packages_to_files[package].push_back(std::string(filename));
    }
}

std::string
CodeProvenance::serialize_to_json_str()
{
    std::lock_guard<std::mutex> lock(mtx);

    if (!enabled) {
        return "";
    }

    std::ostringstream out;

    // TODO(taegyunkim): Use a JSON library to serialize this
    out << "{\"v1\":[";
    for (const auto& [package, paths] : packages_to_files) {
        out << "{";
        out << "\"name\": \"" << package->name << "\",";
        out << "\"kind\": \"" << package->kind << "\",";
        out << "\"version\": \"" << package->version << "\",";
        out << "\"paths\":[";
        for (const auto& path : paths) {
            out << "\"" << path << "\""
                << ",";
        }
        out << "]";
        out << "},";
    }
    out << "]}";
    packages_to_files.clear();
    return out.str();
}

void
CodeProvenance::reset()
{
    std::lock_guard<std::mutex> lock(mtx);
    packages.clear(); // Maybe this is unnecessary?
    packages_to_files.clear();
}

std::string
CodeProvenance::get_package_name(std::string_view filename)
{
    std::regex regex_pattern(R"(site-packages/([^/]+))");
    std::string filename_str = std::string(filename);
    std::smatch match;

    if (std::regex_search(filename_str, match, regex_pattern)) {
        return std::string(filename.data() + match.position(1), match.length(1));
    } else {
        return STDLIB;
    }
}

std::string
CodeProvenance::get_site_packages_path(std::string_view filename)
{
    std::string site_packages = "site-packages";

    // Find the position of "site-packages" in the full path
    size_t pos = filename.find(site_packages);

    if (pos != std::string::npos) {
        // Extract the path up to and including "site-packages"
        return std::string(filename.substr(0, pos + site_packages.length()));
    }

    // Return an empty string if "site-packages" is not found
    return "";
}

std::string
CodeProvenance::get_package_version(std::string_view filename, std::string_view package_name)
{
    std::string site_packages_path = get_site_packages_path(filename);
    if (site_packages_path.empty()) {
        return "";
    }

    std::regex dist_info_pattern(std::string(package_name) + "-(.+)\\.dist-info");
    for (const auto& entry : std::filesystem::directory_iterator(site_packages_path)) {
        const std::string entry_name = entry.path().filename().string();
        std::smatch match;

        if (std::regex_search(entry_name, match, dist_info_pattern)) {
            return match[1].str();
        }
    }
    return "";
}

const Package*
CodeProvenance::get_package(std::string_view filename)
{
    std::string package_name = get_package_name(filename);

    auto it = packages.find(package_name);

    if (it == packages.end()) {
        return nullptr;
    } else {
        return it->second.get();
    }
}

const Package*
CodeProvenance::add_new_package(std::string package_name, std::string version)
{
    std::unique_ptr<Package> package = std::make_unique<Package>();
    package->name = package_name;
    package->kind = "library";
    package->version = version;

    const Package* ret_val = package.get();
    packages[package_name] = std::move(package);

    return ret_val;
}

}
