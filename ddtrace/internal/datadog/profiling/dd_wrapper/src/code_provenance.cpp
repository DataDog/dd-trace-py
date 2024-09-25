#include "code_provenance.hpp"

#include <memory>
#include <mutex>
#include <sstream>
#include <string>
#include <string_view>
#include <unordered_map>
#include <vector>

namespace Datadog {

void
Datadog::CodeProvenance::lock()
{
    mtx.lock();
}

void
Datadog::CodeProvenance::unlock()
{
    mtx.unlock();
}

void
Datadog::CodeProvenance::postfork_child()
{
    get_instance().mtx.~mutex();            // Destroy the mutex
    new (&get_instance().mtx) std::mutex(); // Recreate the mutex
    get_instance().reset();                 // Reset the state
}

void
Datadog::CodeProvenance::prefork()
{
    get_instance().lock();
}

void
Datadog::CodeProvenance::postfork_parent()
{
    get_instance().unlock();
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
    std::lock_guard<std::mutex> lock(mtx);

    if (!enabled) {
        return;
    }

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
    std::lock_guard<std::mutex> lock(mtx);

    if (!enabled) {
        return;
    }

    std::string_view package_name = get_package_name(filename);
    if (package_name.empty() or package_name == STDLIB) {
        return;
    }

    auto it = packages.find(package_name);
    if (it == packages.end()) {
        return;
    }

    const Package* package = it->second.get();
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
    out << "{\"v1\":["; // Start of the JSON array
    for (const auto& [package, paths] : packages_to_files) {
        out << "{"; // Start of the JSON object
        out << "\"name\": \"" << package->name << "\",";
        out << "\"kind\": \"library\",";
        out << "\"version\": \"" << package->version << "\",";
        out << "\"paths\":["; // Start of paths array
        for (const auto& path : paths) {
            out << "\"" << path << "\""
                << ",";
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
    out << "}"; // End of stdlib JSON object

    out << "]}"; // End of the JSON array
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
