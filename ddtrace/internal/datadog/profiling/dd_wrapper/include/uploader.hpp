#pragma once

#include "sample.hpp"
#include "types.hpp"

#include <memory>
#include <mutex>

extern "C"
{
#include "datadog/profiling.h"
}

namespace Datadog {

struct DdogProfExporterDeleter
{
    void operator()(ddog_prof_Exporter* ptr) const;
};

class Uploader
{
  private:
    static inline std::mutex upload_lock{};

  public:
    size_t profile_seq = 0;
    std::string runtime_id;
    std::unique_ptr<ddog_prof_Exporter, DdogProfExporterDeleter> ddog_exporter;
    std::string url;

    std::string errmsg;

    static inline ddog_CancellationToken* cancel{ nullptr };

  public:
    Uploader(std::string_view _url, ddog_prof_Exporter* ddog_exporter);
    bool upload(ddog_prof_Profile& profile);
    static void cancel_inflight();
    static void lock();
    static void unlock();
};

} // namespace Datadog
