#pragma once

#include "sample.hpp"
#include "sample_builder.hpp"
#include "uploader_builder.hpp"

#include <map>
#include <mutex>
#include <thread>

namespace Datadog {

// Global state.  Manages thread-keys caches of samples
class GlobalCache
{
  private:
    std::mutex sample_storage_mtx;
    std::map<std::thread::id, Sample> sample_cache;

    // TODO delete some constructors?
    //  GlobalCache();
    //  GlobalCache(const GlobalCache &) = delete;
    //  GlobalCache &operator=(const GlobalCache &) = delete;
    //  GlobalCache(GlobalCache &&) = delete;

  public:
    inline static UploaderBuilder uploader_builder{};
    inline static SampleBuilder sample_builder{};

    static GlobalCache& get_singleton();
    static Sample& get(std::thread::id id);
    static void clear();
};

} // namespace Datadog
