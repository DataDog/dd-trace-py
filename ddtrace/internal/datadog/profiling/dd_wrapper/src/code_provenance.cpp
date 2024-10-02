#include "code_provenance.hpp"

#include <memory>
#include <mutex>
#include <set>
#include <sstream>
#include <string>
#include <string_view>
#include <unordered_map>
#include <vector>

namespace Datadog {

void
Datadog::CodeProvenance::postfork_child()
{
    get_instance().mtx.~mutex();            // Destroy the mutex
    new (&get_instance().mtx) std::mutex(); // Recreate the mutex
    get_instance().reset();                 // Reset the state
}

void
Datadog::CodeProvenance::set_enabled(bool enable)
{
    this->enabled.store(enable);
}

bool
Datadog::CodeProvenance::is_enabled()
{
    return enabled.load();
}

void
Datadog::CodeProvenance::set_runtime_version(std::string_view _runtime_version)
{
    std::lock_guard<std::mutex> lock(mtx);
    this->runtime_version = _runtime_version;
}

void
Datadog::CodeProvenance::set_stdlib_path(std::string_view _stdlib_path)
{
    std::lock_guard<std::mutex> lock(mtx);
    this->stdlib_path = _stdlib_path;
}

void
CodeProvenance::add_packages(std::unordered_map<std::string_view, std::string_view> distributions)
{

    if (!is_enabled()) {
        return;
    }

    std::lock_guard<std::mutex> lock(mtx);

    for (const auto& [package_name, version] : distributions) {
        auto it = packages.find(package_name);
        if (it == packages.end()) {
            add_new_package(package_name, version);
        }
    }
}

void
CodeProvenance::add_filename(std::string_view filename)
{
    if (!is_enabled()) {
        return;
    }

    std::string_view package_name = get_package_name(filename);
    if (package_name.empty() or package_name == STDLIB) {
        return;
    }

    std::lock_guard<std::mutex> lock(mtx);

    auto it = packages.find(package_name);
    if (it == packages.end()) {
        return;
    }

    const Package* package = it->second.get();
    if (package) {
        if (packages_to_files.find(package) == packages_to_files.end()) {
            packages_to_files[package] = std::set<std::string>();
        }
        packages_to_files[package].insert(std::string(filename));
    }
}

std::optional<std::string>
CodeProvenance::try_serialize_to_json_str()
{
    if (!is_enabled()) {
        return std::nullopt;
    }

    std::lock_guard<std::mutex> lock(mtx);

    std::ostringstream out;
    // DEV: Simple JSON serialization, maybe consider using a JSON library.
    out << "{\"v1\":["; // Start of the JSON array
    for (const auto& [package, paths] : packages_to_files) {
        out << "{"; // Start of the JSON object
        out << "\"name\": \"" << package->name << "\",";
        out << "\"kind\": \"library\",";
        out << "\"version\": \"" << package->version << "\",";
        out << "\"paths\":["; // Start of paths array
        for (auto it = paths.begin(); it != paths.end(); ++it) {
            out << "\"" << *it << "\"";
            if (std::next(it) != paths.end()) {
                out << ",";
            }
        }
        out << "]";  // End of paths array
        out << "},"; // End of the JSON object
    }
    // Add python runtime information
    out << "{"; // Start of stdlib JSON object
    out << "\"name\": \"stdlib\",";
    out << "\"kind\": \"standard library\",";
    out << "\"version\": \"" << runtime_version << "\",";
    out << "\"paths\":[";
    out << "\"" << stdlib_path << "\"";
    out << "]";
    out << "}";  // End of stdlib JSON object
    out << "]}"; // End of the JSON array

    // Clear the state
    packages_to_files.clear();
    return out.str();
}

void
CodeProvenance::reset()
{
    std::lock_guard<std::mutex> lock(mtx);
    packages_to_files.clear();
}

std::string_view
CodeProvenance::get_package_name(std::string_view filename)
{
    // std::regex is slow, so we use a simple string search
    static const std::string site_packages = "site-packages/";

    size_t start = filename.find(site_packages);
    if (start == std::string::npos) {
        return std::string_view(STDLIB);
    }

    start += site_packages.length();
    size_t end = filename.find('/', start);
    if (end == std::string::npos) {
        return {};
    }

    return filename.substr(start, end - start);
}

const Package*
CodeProvenance::add_new_package(std::string_view package_name, std::string_view version)
{
    std::unique_ptr<Package> package = std::make_unique<Package>();
    package->name = package_name;
    package->version = version;

    const Package* ret_val = package.get();
    packages[std::string_view(package->name)] = std::move(package);

    return ret_val;
}

}
