#pragma once

#include "libdatadog_helpers.hpp"

#include <optional>
#include <string>

namespace Datadog {
class Crashtracker {
  private:
    bool collect_stacktrace;
    bool create_alt_stack;
    std::string url;
    std::optional<std::string> stderr_filename{std::nullopt};
    std::optional<std::string> stdout_filename{std::nullopt};
    std::string path_to_receiver_binary;
    ddog_prof_CrashtrackerResolveFrames resolve_frames;

    std::string library_name;
    std::string library_version;
    std::string family;
  public:

    // Setters
    void set_collect_stacktrace(bool _collect_stacktrace);
    void set_create_alt_stack(bool _create_alt_stack);
    void set_url(const std::string &_url);
    void set_stderr_filename(const std::string &_stderr_filename);
    void set_stdout_filename(const std::string &_stdout_filename);
    void set_path_to_receiver_binary(const std::string &_path_to_receiver_binary);

    void set_resolve_frames(ddog_prof_CrashtrackerResolveFrames _resolve_frames);
    void set_library_name(const std::string &_library_name);
    void set_library_version(const std::string &_library_version);
    void set_family(const std::string &_family);

    // Helpers
    bool init();

};

} // namespace Datadog
