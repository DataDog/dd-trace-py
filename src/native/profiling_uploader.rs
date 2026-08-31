use std::collections::HashMap;
use std::fs;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Mutex;
use std::time::{Duration, SystemTime};

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

use libdd_common::tag::Tag;
use libdd_profiling::exporter::{config, File as ExporterFile, ProfileExporter};
use libdd_profiling::internal::{EncodedProfile, ProfiledEndpointsStats};
use tokio_util::sync::CancellationToken;

fn to_py_err(e: anyhow::Error) -> PyErr {
    PyValueError::new_err(e.to_string())
}

fn ns_to_systemtime(ns: i64) -> PyResult<SystemTime> {
    if ns >= 0 {
        SystemTime::UNIX_EPOCH
            .checked_add(Duration::from_nanos(ns as u64))
            .ok_or_else(|| PyValueError::new_err("timestamp overflowed SystemTime"))
    } else {
        SystemTime::UNIX_EPOCH
            .checked_sub(Duration::from_nanos((-ns) as u64))
            .ok_or_else(|| PyValueError::new_err("timestamp underflowed SystemTime"))
    }
}

/// Upload sequence number, mirroring `ProfilerState::upload_seq` in dd_wrapper -- used only to
/// give file-output dumps unique names, matching `Uploader::export_to_file`'s existing
/// `<output_filename>.<pid>.<seq>` convention so tools/tests reading `DD_PROFILING_OUTPUT_PPROF`
/// keep working regardless of which upload path produced the file.
static UPLOAD_SEQ: AtomicU64 = AtomicU64::new(0);

/// Cancellation token for whatever upload is currently in flight, mirroring
/// `ProfilerState::upload_cancel`. Before starting a new upload we install our own token here
/// and cancel whatever was there before -- same "only the newest upload matters" semantics as
/// `Uploader::upload_unlocked()`. We deliberately don't clear our own entry on completion (see
/// comment in `send_blocking`): the cost is a harmless no-op `cancel()` on an already-finished
/// token the next time this runs.
static UPLOAD_CANCEL: Mutex<Option<CancellationToken>> = Mutex::new(None);

/// Pilot PyO3 binding for the profiling upload/export path (see
/// dd_wrapper/replace-cython-wrappers-with-pyo3-plan.md). Wraps libdd-profiling's safe
/// `ProfileExporter` directly, bypassing the dd_wrapper C++ Uploader/UploaderBuilder and the
/// libdatadog C ABI for this one slice.
///
/// A new instance is constructed for every upload (mirroring `UploaderBuilder::build()`, which
/// also builds a fresh `ProfileExporter` every upload cycle from the profiler's current
/// config/tags) rather than being kept alive and mutated across calls -- this sidesteps having
/// to invent a mutation API for the per-upload dynamic tags (runtime_id, pid, process_type) and
/// keeps parity with the existing rebuild-every-cycle behavior.
#[pyclass(name = "ProfileUploader", module = "ddtrace.internal._native")]
pub struct ProfileUploaderPy {
    inner: Option<ProfileExporter>,
    // When set, send_blocking() writes the pprof/metadata/info payloads straight to disk using
    // dd_wrapper's existing `Uploader::export_to_file` naming convention, instead of going
    // through `inner`. Kept separate from the `file://` URL scheme below (which is
    // libdatadog's own debug-dump-the-whole-HTTP-request mechanism, a different format) because
    // `DD_PROFILING_OUTPUT_PPROF` is a documented, test-depended-on output format.
    output_filename: Option<String>,
}

#[pymethods]
impl ProfileUploaderPy {
    #[new]
    #[pyo3(signature = (library_name, library_version, family, url, tags=Vec::new(), timeout_ms=None, output_filename=None))]
    fn new(
        library_name: &str,
        library_version: &str,
        family: &str,
        url: &str,
        tags: Vec<(String, String)>,
        timeout_ms: Option<u64>,
        output_filename: Option<String>,
    ) -> PyResult<Self> {
        if let Some(output_filename) = output_filename {
            return Ok(ProfileUploaderPy {
                inner: None,
                output_filename: Some(output_filename),
            });
        }

        let mut endpoint = if let Some(path) = url.strip_prefix("file://") {
            config::file(path).map_err(to_py_err)?
        } else {
            let uri: http::Uri = url
                .parse()
                .map_err(|e: http::uri::InvalidUri| PyValueError::new_err(e.to_string()))?;
            config::agent(uri).map_err(to_py_err)?
        };

        if let Some(timeout_ms) = timeout_ms {
            endpoint.timeout_ms = timeout_ms;
        }

        let tags = tags
            .into_iter()
            .filter_map(|(key, value)| Tag::new(key, value).ok())
            .collect();

        let inner = ProfileExporter::new(library_name, library_version, family, tags, endpoint)
            .map_err(to_py_err)?;
        Ok(ProfileUploaderPy {
            inner: Some(inner),
            output_filename: None,
        })
    }

    /// Serializes and sends an already-encoded pprof buffer, releasing the GIL for the
    /// duration of the blocking HTTP call (matching `_ddup.pyx`'s existing
    /// `with nogil: ddup_upload()` behavior).
    ///
    /// `process_tags`, `additional_files` (e.g. code-provenance JSON) and `endpoints_stats`
    /// (span-endpoint hit counts) mirror the corresponding pieces of
    /// `Uploader::upload_unlocked()` in dd_wrapper -- without them, the uploaded event would
    /// silently be missing process tags, code provenance, and endpoint-count aggregation
    /// respectively, even though the request itself would still succeed.
    #[allow(clippy::too_many_arguments)]
    #[pyo3(signature = (
        buffer,
        start_ns,
        end_ns,
        internal_metadata_json=None,
        info_json=None,
        process_tags=None,
        additional_files=Vec::new(),
        endpoints_stats=Vec::new(),
    ))]
    fn send_blocking(
        &mut self,
        py: Python<'_>,
        buffer: Vec<u8>,
        start_ns: i64,
        end_ns: i64,
        internal_metadata_json: Option<&str>,
        info_json: Option<&str>,
        process_tags: Option<&str>,
        additional_files: Vec<(String, Vec<u8>)>,
        endpoints_stats: Vec<(String, i64)>,
    ) -> PyResult<u16> {
        if let Some(output_filename) = self.output_filename.clone() {
            return py.detach(move || {
                write_to_file(&output_filename, &buffer, internal_metadata_json, info_json)
            });
        }

        let inner = self
            .inner
            .as_mut()
            .expect("ProfileUploaderPy: inner exporter missing without output_filename set");

        let start = ns_to_systemtime(start_ns)?;
        let end = ns_to_systemtime(end_ns)?;
        let internal_metadata = internal_metadata_json
            .map(serde_json::from_str)
            .transpose()
            .map_err(|e| PyValueError::new_err(e.to_string()))?;
        let info = info_json
            .map(serde_json::from_str)
            .transpose()
            .map_err(|e| PyValueError::new_err(e.to_string()))?;

        let endpoints_stats: HashMap<String, i64> = endpoints_stats.into_iter().collect();
        let endpoints_stats = ProfiledEndpointsStats::from(endpoints_stats);

        py.detach(move || {
            let files: Vec<ExporterFile> = additional_files
                .iter()
                .map(|(name, bytes)| ExporterFile { name, bytes })
                .collect();

            // Cancel whatever upload was previously in flight, matching
            // Uploader::upload_unlocked()'s cancellation-token exchange.
            let request_cancel = CancellationToken::new();
            {
                let mut guard = UPLOAD_CANCEL.lock().unwrap_or_else(|e| e.into_inner());
                if let Some(previous) = guard.replace(request_cancel.clone()) {
                    previous.cancel();
                }
            }

            let profile = EncodedProfile {
                start,
                end,
                buffer,
                endpoints_stats,
            };

            inner
                .send_blocking(
                    profile,
                    &files,
                    &[],
                    internal_metadata,
                    info,
                    process_tags,
                    Some(&request_cancel),
                )
                .map(|status| status.as_u16())
                .map_err(to_py_err)
        })
    }
}

fn write_to_file(
    output_filename: &str,
    buffer: &[u8],
    internal_metadata_json: Option<&str>,
    info_json: Option<&str>,
) -> PyResult<u16> {
    // Matches Uploader::export_to_file's naming convention exactly so anything reading
    // DD_PROFILING_OUTPUT_PPROF output (tests, manual debugging) doesn't care which upload
    // path produced it.
    let pid = std::process::id();
    let seq = UPLOAD_SEQ.fetch_add(1, Ordering::Relaxed);
    let base = format!("{output_filename}.{pid}.{seq}");

    fs::write(format!("{base}.pprof"), buffer).map_err(|e| PyValueError::new_err(e.to_string()))?;
    fs::write(
        format!("{base}.internal_metadata.json"),
        internal_metadata_json.unwrap_or(""),
    )
    .map_err(|e| PyValueError::new_err(e.to_string()))?;
    if let Some(info_json) = info_json {
        if !info_json.is_empty() {
            fs::write(format!("{base}.info.json"), info_json)
                .map_err(|e| PyValueError::new_err(e.to_string()))?;
        }
    }

    Ok(200)
}
