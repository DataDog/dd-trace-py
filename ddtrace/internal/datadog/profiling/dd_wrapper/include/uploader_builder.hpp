#pragma once

#include "uploader.hpp"

#include <atomic>
#include <mutex>
#include <string>
#include <string_view>
#include <unordered_map>
#include <variant>

namespace Datadog {

class UploaderBuilder
{
    static inline std::mutex tag_mutex{};

    static Datadog::UploaderTags profile_metadata;
    inline static std::atomic<uint64_t> upload_seq{ 0 };


  public:
    static void set_env(std::string_view _env);
    static void set_service(std::string_view _service);
    static void set_version(std::string_view _version);
    static void set_runtime(std::string_view _runtime);
    static void set_runtime_version(std::string_view _runtime_version);
    static void set_runtime_id(std::string_view _runtime_id);
    static void set_profiler_version(std::string_view _profiler_version);
    static void set_url(std::string_view _url);
    static bool set_dir(std::string_view _dir);
    static void set_tag(std::string_view _key, std::string_view _val);

    static void clear_url();
    static void clear_dir();

    static std::variant<Datadog::Uploader, std::string> build();
};

} // namespace Datadog
