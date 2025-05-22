#include "uploader.hpp"

#include "code_provenance.hpp"
#include "libdatadog_helpers.hpp"

#include <errno.h> // errno
#include <fstream> // ofstream
#include <optional>
#include <sstream>  // ostringstream
#include <string.h> // strerror
#ifdef _WIN32
#include <io.h>
#else
#include <unistd.h> // getpid
#endif
#include <vector>

using namespace Datadog;

Datadog::Uploader::Uploader(ddog_prof_ProfileExporter _ddog_exporter)
  : ddog_exporter{ _ddog_exporter }
{
    // Increment the upload sequence number every time we build an uploader.
    // Upoloaders are use-once-and-destroy.
    upload_seq++;
}

bool
Datadog::Uploader::export_to_file(std::unique_ptr<std::string> output_filename, ddog_prof_Profile& profile)
{
    if (!output_filename) {
        std::cerr << "Error: output_filename is null" << std::endl;
        return false;
    }

    // Serialize the profile
    ddog_prof_Profile_SerializeResult serialize_result = ddog_prof_Profile_serialize(&profile, nullptr, nullptr);
    if (serialize_result.tag != DDOG_PROF_PROFILE_SERIALIZE_RESULT_OK) {
        auto err = serialize_result.err;
        std::cerr << "Error serializing pprof: " << err_to_msg(&err, "Error serializing pprof") << std::endl;
        ddog_Error_drop(&err);
        return false;
    }
    ddog_prof_EncodedProfile* encoded = &serialize_result.ok;

    // Write the profile to a file using the following format for filename:
    // <output_filename>.<process_id>.<sequence_number>
    std::ostringstream oss;
    oss << output_filename.get() << "." << getpid() << "." << upload_seq;
    std::string filename = oss.str();
    std::ofstream out(filename, std::ios::binary);
    if (!out.is_open()) {
        std::cerr << "Error opening output file " << filename << ": " << strerror(errno) << std::endl;
        return false;
    }
    auto bytes_res = ddog_prof_EncodedProfile_bytes(encoded);
    if (bytes_res.tag == DDOG_PROF_RESULT_BYTE_SLICE_ERR_BYTE_SLICE) {
        std::cerr << "Error getting bytes from encoded profile: "
                  << err_to_msg(&bytes_res.err, "Error getting bytes from encoded profile") << std::endl;
        ddog_Error_drop(&bytes_res.err);
        return false;
    }
    out.write(reinterpret_cast<const char*>(bytes_res.ok.ptr), bytes_res.ok.len);
    if (out.fail()) {
        std::cerr << "Error writing to output file " << filename << ": " << strerror(errno) << std::endl;
        return false;
    }
    return true;
}

bool
Datadog::Uploader::upload(ddog_prof_Profile& profile)
{
    // Serialize the profile
    ddog_prof_Profile_SerializeResult serialize_result = ddog_prof_Profile_serialize(&profile, nullptr, nullptr);
    if (serialize_result.tag !=
        DDOG_PROF_PROFILE_SERIALIZE_RESULT_OK) { // NOLINT (cppcoreguidelines-pro-type-union-access)
        auto err = serialize_result.err;         // NOLINT (cppcoreguidelines-pro-type-union-access)
        errmsg = err_to_msg(&err, "Error serializing pprof");
        std::cerr << errmsg << std::endl;
        ddog_Error_drop(&err);
        return false;
    }
    ddog_prof_EncodedProfile* encoded = &serialize_result.ok; // NOLINT (cppcoreguidelines-pro-type-union-access)

    std::vector<ddog_prof_Exporter_File> to_compress_files;

    std::string_view json_str = CodeProvenance::get_instance().get_json_str();

    if (!json_str.empty()) {
        to_compress_files.reserve(1);
        to_compress_files.push_back({
          .name = to_slice("code-provenance.json"),
          .file = to_byte_slice(json_str),
        });
    }

    auto build_res = ddog_prof_Exporter_Request_build(
      &ddog_exporter,
      encoded,
      // files_to_compress_and_export
      {
        .ptr = reinterpret_cast<const ddog_prof_Exporter_File*>(to_compress_files.data()),
        .len = static_cast<uintptr_t>(to_compress_files.size()),
      },
      ddog_prof_Exporter_Slice_File_empty(), // files_to_export_unmodified
      nullptr,                               // optional_additional_tags
      nullptr,                               // optional_internal_metadata_json
      nullptr                                // optional_info_json
    );
    ddog_prof_EncodedProfile_drop(encoded);

    if (build_res.tag ==
        DDOG_PROF_REQUEST_RESULT_ERR_HANDLE_REQUEST) { // NOLINT (cppcoreguidelines-pro-type-union-access)
        auto err = build_res.err;                      // NOLINT (cppcoreguidelines-pro-type-union-access)
        errmsg = err_to_msg(&err, "Error building request");
        std::cerr << errmsg << std::endl;
        ddog_Error_drop(&err);
        return false;
    }

    bool ret = true;
    // The upload operation sets up some global state in libdatadog (the tokio runtime), so
    // we ensure exclusivity here.
    {
        // If we're here, we're about to create a new upload, so cancel any inflight ones
        const std::lock_guard<std::mutex> lock_guard(upload_lock);
        cancel_inflight();

        // We have to create a new cancellation token, as cancel_inflight() drops the previous one.
        // We also clone it to pass it to the request.
        cancel = ddog_CancellationToken_new();
        auto cancel_for_request = ddog_CancellationToken_clone(&cancel);

        // Build and check the response object
        ddog_prof_Request* req = &build_res.ok; // NOLINT (cppcoreguidelines-pro-type-union-access)
        ddog_prof_Result_HttpStatus res = ddog_prof_Exporter_send(&ddog_exporter, req, &cancel_for_request);
        if (res.tag ==
            DDOG_PROF_RESULT_HTTP_STATUS_ERR_HTTP_STATUS) { // NOLINT (cppcoreguidelines-pro-type-union-access)
            auto err = res.err;                             // NOLINT (cppcoreguidelines-pro-type-union-access)
            errmsg = err_to_msg(&err, "Error uploading");
            std::cerr << errmsg << std::endl;
            ddog_Error_drop(&err);
            ret = false;
        }
        ddog_prof_Exporter_Request_drop(req);
        ddog_CancellationToken_drop(&cancel_for_request);
    }

    return ret;
}

void
Datadog::Uploader::lock()
{
    upload_lock.lock();
}

void
Datadog::Uploader::unlock()
{
    upload_lock.unlock();
}

void
Datadog::Uploader::cancel_inflight()
{
    ddog_CancellationToken_cancel(&cancel);
    ddog_CancellationToken_drop(&cancel);
}

void
Datadog::Uploader::prefork()
{
    lock();
    cancel_inflight();
}

void
Datadog::Uploader::postfork_parent()
{
    unlock();
}

void
Datadog::Uploader::postfork_child()
{
    // NB placement-new to re-init and leak the mutex because doing anything else is UB
    new (&upload_lock) std::mutex();
}
