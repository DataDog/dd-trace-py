// Unless explicitly stated otherwise all files in this repository are licensed
// under the Apache License Version 2.0. This product includes software
// developed at Datadog (https://www.datadoghq.com/). Copyright 2021-Present
// Datadog, Inc.

#include "uploader.hpp"
#include "libdatadog_helpers.hpp"

using namespace Datadog;

void
DdogProfExporterDeleter::operator()(ddog_prof_Exporter* ptr) const
{
    ddog_prof_Exporter_drop(ptr);
}

Uploader::Uploader(std::string_view _url, ddog_prof_Exporter* _ddog_exporter)
  : ddog_exporter{ _ddog_exporter }
  , url{ _url }
{
}

bool
Uploader::upload(ddog_prof_Profile& profile)
{
    // Serialize the profile
    ddog_prof_Profile_SerializeResult result = ddog_prof_Profile_serialize(&profile, nullptr, nullptr, nullptr);
    if (result.tag != DDOG_PROF_PROFILE_SERIALIZE_RESULT_OK) {
        errmsg = err_to_msg(&result.err, "Error serializing pprof");
        ddog_Error_drop(&result.err);
        std::cout << errmsg << std::endl;
        return false;
    }
    ddog_prof_EncodedProfile* encoded = &result.ok;

    // If we have any custom tags, set them now
    ddog_Vec_Tag tags = ddog_Vec_Tag_new();
    add_tag(tags, ExportTagKey::profile_seq, std::to_string(profile_seq), errmsg);
    add_tag(tags, ExportTagKey::runtime_id, runtime_id, errmsg);

    // Build the request object
    ddog_prof_Exporter_File file = {
        .name = to_slice("auto.pprof"),
        .file = ddog_Vec_U8_as_slice(&encoded->buffer),
    };
    const uint64_t max_timeout_ms = 5000; // 5s is a common timeout parameter for Datadog profilers

    auto build_res = ddog_prof_Exporter_Request_build(ddog_exporter.get(),
                                                      encoded->start,
                                                      encoded->end,
                                                      ddog_prof_Exporter_Slice_File_empty(),
                                                      { .ptr = &file, .len = 1 },
                                                      &tags,
                                                      nullptr,
                                                      nullptr,
                                                      max_timeout_ms);
    ddog_prof_EncodedProfile_drop(encoded);

    if (build_res.tag == DDOG_PROF_EXPORTER_REQUEST_BUILD_RESULT_ERR) {
        errmsg = err_to_msg(&build_res.err, "Error building request");
        ddog_Error_drop(&build_res.err);
        ddog_Vec_Tag_drop(tags);
        std::cout << errmsg << std::endl;
        return false;
    }

    // Build and check the response object
    ddog_prof_Exporter_Request* req = build_res.ok;
    ddog_prof_Exporter_SendResult res = ddog_prof_Exporter_send(ddog_exporter.get(), &req, nullptr);
    if (res.tag == DDOG_PROF_EXPORTER_SEND_RESULT_ERR) {
        std::string ddog_err(ddog_Error_message(&res.err).ptr);
        errmsg = err_to_msg(&res.err, "Error uploading: ") + ddog_err;
        ddog_Error_drop(&res.err);
        ddog_Vec_Tag_drop(tags);
        std::cout << errmsg << std::endl;
        return false;
    }

    // Cleanup
    ddog_prof_Exporter_Request_drop(&req);
    ddog_Vec_Tag_drop(tags);

    return true;
}
