// Unless explicitly stated otherwise all files in this repository are licensed
// under the Apache License Version 2.0. This product includes software
// developed at Datadog (https://www.datadoghq.com/). Copyright 2021-Present
// Datadog, Inc.

#pragma once

#include "profile.hpp"
#include "types.hpp"

extern "C"
{
#include "datadog/profiling.h"
}

namespace Datadog {

class Uploader
{
    bool agentless; // Whether or not to actually use API key/intake
    size_t profile_seq = 0;
    std::string runtime_id;
    std::unique_ptr<ddog_prof_Exporter, DdogProfExporterDeleter> ddog_exporter;
    std::string url;

    std::string errmsg;

  public:
    Uploader(std::string_view _url, ddog_prof_Exporter* ddog_exporter);
    bool upload(ddog_prof_Profile& profile);
};

} // namespace Datadog
