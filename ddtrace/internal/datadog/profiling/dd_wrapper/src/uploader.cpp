#include "uploader.hpp"

#include "code_provenance.hpp"
#include "libdatadog_helpers.hpp"
#include "profiler_state.hpp"
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
    ProfilerState::get().upload_seq++;
}

Datadog::Uploader::~Uploader()
{
    // We need to call _drop() on the exporter and the cancellation token,
    // as their inner pointers are allocated on the Rust side. And since
    // there could be a request in flight, we first need to cancel it. Then,
    // we drop the exporter and the cancellation token.
    auto current_cancel = ProfilerState::get().upload_cancel.exchange({ .inner = nullptr });

    if (current_cancel.inner != nullptr) {
        ddog_CancellationToken_cancel(&current_cancel);
        ddog_CancellationToken_drop(&current_cancel);
    }

    ddog_prof_Exporter_drop(&ddog_exporter);
    ddog_prof_EncodedProfile_drop(&encoded_profile);
}

bool
Datadog::Uploader::export_to_file(ddog_prof_EncodedProfile& encoded, std::string_view internal_metadata_json)
{
    // Write the profile to a file using the following format for filename:
    // <output_filename>.<process_id>.<sequence_number>
    std::ostringstream oss;
    oss << output_filename << "." << getpid() << "." << ProfilerState::get().upload_seq.load();
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
Datadog::Uploader::upload_unlocked()
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

    ddog_prof_Exporter_Slice_File files_to_compress = {
        .ptr = reinterpret_cast<const ddog_prof_Exporter_File*>(to_compress_files.data()),
        .len = static_cast<uintptr_t>(to_compress_files.size()),
    };

    bool ret = true;
    // Before starting a new upload, we need to cancel the current one (if it exists).
    // To do that, we exchange the current cancellation token with a new one (which will be used for our own upload)
    // If the current cancellation token was not null, then we use it to cancel the ongoing upload, then drop it.
    // Other threads can use our own cancellation token to cancel our upload as soon as we have exchanged it.
    // Note: we need to make (and use) a clone of the new cancellation token for the request, in case another thread
    // cancels our upload and drops the handle (which would free the token).
    auto new_cancel = ddog_CancellationToken_new();
    auto new_cancel_clone_for_request = ddog_CancellationToken_clone(&new_cancel);
    auto current_cancel = ProfilerState::get().upload_cancel.exchange(new_cancel);

    if (current_cancel.inner != nullptr) {
        ddog_CancellationToken_cancel(&current_cancel);
        ddog_CancellationToken_drop(&current_cancel);
    }

    // Build and send the request in one call
    ddog_prof_Result_HttpStatus res = ddog_prof_Exporter_send_blocking(&ddog_exporter,
                                                                       &encoded_profile,
                                                                       files_to_compress,
                                                                       nullptr, // optional_additional_tags
                                                                       optional_process_tags_ptr,
                                                                       &internal_metadata_json_slice,
                                                                       nullptr, // optional_info_json
                                                                       &new_cancel_clone_for_request);

    if (res.tag == DDOG_PROF_RESULT_HTTP_STATUS_ERR_HTTP_STATUS) { // NOLINT (cppcoreguidelines-pro-type-union-access)
        auto err = res.err;                                        // NOLINT (cppcoreguidelines-pro-type-union-access)
        errmsg = err_to_msg(&err, "Error uploading");
        std::cerr << errmsg << std::endl;
        ddog_Error_drop(&err);
        ret = false;
    }
    ddog_CancellationToken_drop(&new_cancel_clone_for_request);
    ddog_prof_Exporter_drop(&ddog_exporter);

    return ret;
}

bool
Datadog::Uploader::upload()
{
    // The upload operation sets up some global state in libdatadog (the tokio runtime), so
    // we ensure exclusivity here.
    const std::lock_guard<std::mutex> lock_guard(ProfilerState::get().upload_lock);
    return upload_unlocked();
}

void
Datadog::Uploader::lock()
{
    ProfilerState::get().upload_lock.lock();
}

void
Datadog::Uploader::unlock()
{
    ProfilerState::get().upload_lock.unlock();
}

void
Datadog::Uploader::cancel_inflight()
{
    // Cancel the current upload if there is one.
    // We replace the cancellation token with a null token as we don't have anything
    // else to provide (here, we are not starting a new upload).
    auto current_cancel = ProfilerState::get().upload_cancel.exchange({ .inner = nullptr });

    if (current_cancel.inner != nullptr) {
        ddog_CancellationToken_cancel(&current_cancel);
        ddog_CancellationToken_drop(&current_cancel);
    }
}
