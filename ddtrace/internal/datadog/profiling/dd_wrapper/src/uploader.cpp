#include "uploader.hpp"

#include "code_provenance.hpp"
#include "libdatadog_helpers.hpp"
#include "profiler_stats.hpp"

#include <cerrno>   // errno
#include <cstring>  // strerror
#include <fstream>  // ofstream
#include <sstream>  // ostringstream
#include <unistd.h> // getpid
#include <vector>

using namespace Datadog;

Datadog::Uploader::Uploader(std::string_view _output_filename,
                            ddog_prof_ProfileExporter _ddog_exporter,
                            ddog_prof_EncodedProfile _encoded_profile,
                            Datadog::ProfilerStats _stats,
                            std::string_view _process_tags)
  : output_filename{ _output_filename }
  , ddog_exporter{ _ddog_exporter }
  , encoded_profile{ _encoded_profile }
  , profiler_stats{ _stats }
  , process_tags{ _process_tags }
{
    // Increment the upload sequence number every time we build an uploader.
    // Uploaders are use-once-and-destroy.
    upload_seq++;
}

bool
Datadog::Uploader::export_to_file(ddog_prof_EncodedProfile& encoded, std::string_view internal_metadata_json)
{
    // Write the profile to a file using the following format for filename:
    // <output_filename>.<process_id>.<sequence_number>
    std::ostringstream oss;
    oss << output_filename << "." << getpid() << "." << upload_seq;
    const std::string base_filename = oss.str();
    const std::string pprof_filename = base_filename + ".pprof";

    std::ofstream out(pprof_filename, std::ios::binary);
    if (!out.is_open()) {
        std::cerr << "Error opening output file " << pprof_filename << ": " << strerror(errno) << std::endl;
        return false;
    }

    auto bytes_res = ddog_prof_EncodedProfile_bytes(&encoded);
    if (bytes_res.tag == DDOG_PROF_RESULT_BYTE_SLICE_ERR_BYTE_SLICE) {
        std::cerr << "Error getting bytes from encoded profile: "
                  << err_to_msg(&bytes_res.err, "Error getting bytes from encoded profile") << std::endl;
        ddog_Error_drop(&bytes_res.err);
        return false;
    }
    out.write(reinterpret_cast<const char*>(bytes_res.ok.ptr), bytes_res.ok.len);
    if (out.fail()) {
        std::cerr << "Error writing to output file " << pprof_filename << ": " << strerror(errno) << std::endl;
        return false;
    }

    const std::string internal_metadata_filename = base_filename + ".internal_metadata.json";
    std::ofstream out_internal_metadata(internal_metadata_filename);
    out_internal_metadata << internal_metadata_json;
    if (out_internal_metadata.fail()) {
        std::cerr << "Error writing to internal metadata file " << internal_metadata_filename << ": " << strerror(errno)
                  << std::endl;
        return false;
    }

    return true;
}

bool
Datadog::Uploader::upload()
{
    if (!output_filename.empty()) {
        return export_to_file(encoded_profile, profiler_stats.get_internal_metadata_json());
    }

    std::vector<ddog_prof_Exporter_File> to_compress_files;

    std::string_view json_str = CodeProvenance::get_instance().get_json_str();

    if (!json_str.empty()) {
        to_compress_files.reserve(1);
        to_compress_files.push_back({
          .name = to_slice("code-provenance.json"),
          .file = to_byte_slice(json_str),
        });
    }

    auto internal_metadata_json = profiler_stats.get_internal_metadata_json();
    auto internal_metadata_json_slice = to_slice(internal_metadata_json);

    ddog_CharSlice process_tags_slice;
    const ddog_CharSlice* optional_process_tags_ptr = nullptr;
    if (!process_tags.empty()) {
        process_tags_slice = to_slice(process_tags);
        optional_process_tags_ptr = &process_tags_slice;
    }

    auto build_res = ddog_prof_Exporter_Request_build(
      &ddog_exporter,
      &encoded_profile,
      // files_to_compress_and_export
      {
        .ptr = reinterpret_cast<const ddog_prof_Exporter_File*>(to_compress_files.data()),
        .len = static_cast<uintptr_t>(to_compress_files.size()),
      },
      ddog_prof_Exporter_Slice_File_empty(), // files_to_export_unmodified
      nullptr,                               // optional_additional_tags
      optional_process_tags_ptr,
      &internal_metadata_json_slice,
      nullptr // optional_info_json
    );

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
        const std::lock_guard<std::mutex> lock_guard(upload_lock);

        // If we're here, we're about to create a new upload, so cancel any inflight ones.
        // Use atomic exchange to swap in a new cancellation token.
        // This is lock-free and fork-safe since we don't hold any additional locks.
        ddog_CancellationToken old_cancel = cancel.exchange(ddog_CancellationToken_new());
        
        // Cancel and drop the old token (these operations are thread-safe)
        ddog_CancellationToken_cancel(&old_cancel);
        ddog_CancellationToken_drop(&old_cancel);

        // Clone the current token for this request
        ddog_CancellationToken current_cancel = cancel.load();
        ddog_CancellationToken cancel_for_request = ddog_CancellationToken_clone(&current_cancel);

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
    // Use exchange to safely get ownership of the current token
    // Exchange with a new token so uploads can continue with fresh cancellation
    ddog_CancellationToken old_cancel = cancel.exchange(ddog_CancellationToken_new());
    
    // Cancel the old token (thread-safe operation, we have exclusive ownership)
    ddog_CancellationToken_cancel(&old_cancel);
    ddog_CancellationToken_drop(&old_cancel);
}

void
Datadog::Uploader::prefork()
{
    // We do not have a way to handle upload state leaking to children, so when a fork happens,
    // we cancel all inflight uploads to prevent this from happening.
    // Note: we could wait for the upload to finish instead of cancelling, but this would
    // mean hanging for some time before the fork happens, which is not acceptable from a
    // user perspective.
    
    // Keep cancelling and trying to acquire the lock until we succeed.
    // This is necessary as a second uploader may start between the end of cancel_inflight and
    // the moment we acquire the lock, which would result on hanging on waiting for the lock.
    // Since we use atomic operations for cancellation, this is now fork-safe (no deadlock risk).
    while (!upload_lock.try_lock()) {
        cancel_inflight();
    }

    // upload_lock is locked and will be unlocked in postfork_parent()
}

void
Datadog::Uploader::postfork_parent()
{
    // upload_lock was locked in prefork.
    unlock();
}

void
Datadog::Uploader::postfork_child()
{
    // NB placement-new to re-init and leak the mutex because doing anything else is UB
    new (&upload_lock) std::mutex();
}
