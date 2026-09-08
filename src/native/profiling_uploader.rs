use std::collections::HashMap;
use std::fs;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::time::{Duration, SystemTime};

use parking_lot::Mutex;

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
/// keep working regardless of which upload path produced the file. The emitted sequence is
/// 1-based, mirroring the C++ pre-increment (see the comment in `write_to_file`).
static UPLOAD_SEQ: AtomicU64 = AtomicU64::new(0);

/// Cancellation token for whatever upload is currently in flight, mirroring
/// `ProfilerState::upload_cancel`. Before starting a new upload we install our own token here
/// and cancel whatever was there before -- same "only the newest upload matters" semantics as
/// `Uploader::upload_unlocked()`. We deliberately don't clear our own entry on completion (see
/// comment in `send_blocking`): the cost is a harmless no-op `cancel()` on an already-finished
/// token the next time this runs.
///
/// Fork safety (see `docs/native-code-review.md` §2): the swap below only ever holds this lock
/// for a handful of in-memory instructions (an `Option::replace` plus a synchronous, non-I/O
/// `CancellationToken::cancel()` notify) -- unlike dd_wrapper's `upload_lock`, it is never held
/// across the blocking HTTP call itself. But POSIX still says a lock held by *any* thread at the
/// instant of `fork()` is undefined in the child, regardless of how short the critical section
/// is. We use `parking_lot::Mutex` (rather than `std::sync::Mutex`) specifically because it
/// exposes `force_unlock()`, which lets `profile_uploader_before_fork`/`*_after_fork_*` below
/// deliberately hold this lock across the actual `fork()` syscall via Python's
/// `os.register_at_fork` (through `ddtrace.internal.forksafe`) -- the same idea as
/// `ProfilerState::prefork()`/`postfork_*` holding `upload_lock` in C++, adapted to a bounded
/// spin (see `UPLOAD_CANCEL_LOCKED_ACROSS_FORK` below) since an atfork `before` handler runs on
/// *every* `fork()` in the process and must never block indefinitely on a lock it doesn't
/// control.
static UPLOAD_CANCEL: Mutex<Option<CancellationToken>> = Mutex::new(None);

/// Set by `profile_uploader_before_fork` iff it actually acquired `UPLOAD_CANCEL` (rather than
/// giving up after `MAX_BEFORE_FORK_ATTEMPTS`); read-and-cleared by the `after_fork_*` hooks so
/// they only call `force_unlock()` -- undefined behavior on an already-unlocked mutex -- when
/// there is really a lock to release.
static UPLOAD_CANCEL_LOCKED_ACROSS_FORK: AtomicBool = AtomicBool::new(false);

/// `os.register_at_fork(before=...)` hook (wired up from Python via
/// `ddtrace.internal.forksafe.register_before_fork`): locks `UPLOAD_CANCEL` and holds it,
/// unreleased, across the upcoming `fork()` -- guaranteeing no thread can be mid-swap when the
/// child is created. Released in `profile_uploader_after_fork_parent`/`_child` below.
///
/// Spins on `try_lock()` with a generous bound rather than blocking forever: this handler runs
/// on every `fork()` in the process (not just profiler-driven ones -- `multiprocessing`,
/// `subprocess` with a fork start method, gunicorn/uWSGI worker spawns all go through it), and
/// the critical section it's waiting out is a few in-memory instructions, so genuinely exceeding
/// the bound means something is very wrong. In that case we proceed without holding the lock
/// across fork() -- a residual, minuscule fork-safety risk -- rather than hang every future
/// fork() in the process forever.
#[pyfunction]
pub fn profile_uploader_before_fork() {
    const MAX_ATTEMPTS: u32 = 10_000;
    for _ in 0..MAX_ATTEMPTS {
        if let Some(guard) = UPLOAD_CANCEL.try_lock() {
            std::mem::forget(guard);
            UPLOAD_CANCEL_LOCKED_ACROSS_FORK.store(true, Ordering::Relaxed);
            return;
        }
        std::thread::yield_now();
    }
    UPLOAD_CANCEL_LOCKED_ACROSS_FORK.store(false, Ordering::Relaxed);
}

/// `os.register_at_fork(after_in_parent=...)` hook. The lock was never actually contended (we
/// hold it ourselves, from the same thread, across the fork), so just release it -- the parent's
/// in-flight-upload bookkeeping, if any, is untouched. No-op if `before_fork` gave up without
/// acquiring the lock.
#[pyfunction]
pub fn profile_uploader_after_fork_parent() {
    if UPLOAD_CANCEL_LOCKED_ACROSS_FORK.swap(false, Ordering::Relaxed) {
        unsafe {
            UPLOAD_CANCEL.force_unlock();
        }
    }
}

/// `os.register_at_fork(after_in_child=...)` hook. Per §2's child-side rule ("recreate from
/// scratch, don't just clear"): the child has no threads and cannot have a real upload in
/// flight (the thread that was doing it, if any, doesn't exist here), so drop whatever token was
/// inherited without cancelling it -- there is nothing on this side of the fork to cancel.
///
/// Writes through the raw `data_ptr()` while the lock is still logically held from
/// `before_fork`, then releases it -- avoids a second lock/unlock cycle (the mutex's state is
/// only well-defined again once `force_unlock()` runs, so acquiring it normally first would be
/// acquiring a lock we just declared to be in an inherited, undefined state).
#[pyfunction]
pub fn profile_uploader_after_fork_child() {
    if UPLOAD_CANCEL_LOCKED_ACROSS_FORK.swap(false, Ordering::Relaxed) {
        unsafe {
            *UPLOAD_CANCEL.data_ptr() = None;
            UPLOAD_CANCEL.force_unlock();
        }
    } else if let Some(mut guard) = UPLOAD_CANCEL.try_lock() {
        // before_fork gave up its spin without acquiring the lock (its holder, if any, doesn't
        // exist in this child), so a blocking `.lock()` here could deadlock forever against a
        // lock nobody will ever release. try_lock() with a best-effort skip is the safe fallback.
        *guard = None;
    }
}

/// Turns the agent URL string `_get_endpoint()` hands us into a libdd-common `Endpoint`.
///
/// The non-`file://` branch goes through `libdd_common::parse_uri` rather than a bare
/// `url.parse::<http::Uri>()` because the C++ path this replaces reaches
/// `ddog_prof_Endpoint_agent`, whose `try_to_url` runs the same encoding: for `unix://` and
/// `windows:` URLs the socket / named-pipe *path* is hex-encoded into the URI **authority**
/// (there is no standard way to spell a filesystem path in a URI, so libdatadog picked this
/// hack -- see `libdd_common::connector::uds::socket_path_to_uri`). A plain `Uri` parse of
/// `unix:///var/run/datadog/apm.socket` instead leaves the socket path sitting in the URI
/// *path*, and `config::agent` then appends `/profiling/v1/input` to it -- every upload from a
/// UDS-configured agent (the norm in k8s / host-agent deployments) would fail. `parse_uri`
/// handles both schemes unconditionally, where the FFI's `try_to_url` cfg-gates each to its own
/// platform; the outcome is identical per platform, and using the ungated helper keeps this to
/// one code path.
///
/// The `file://` arm deliberately stays *before* that call and is NOT the same thing:
/// `config::file` is libdatadog's debug "dump the whole HTTP request to this file" endpoint
/// (what `tests/tracer/test_native_profile_uploader.py` exercises), whereas `parse_uri`'s own
/// `file://` arm hex-encodes the path into an authority for `config::agent`. Collapsing the two
/// branches would silently turn every file-dump test into an attempted agent connection.
fn endpoint_for_url(url: &str) -> PyResult<libdd_common::Endpoint> {
    if let Some(path) = url.strip_prefix("file://") {
        config::file(path).map_err(to_py_err)
    } else {
        let uri = libdd_common::parse_uri(url).map_err(to_py_err)?;
        config::agent(uri).map_err(to_py_err)
    }
}

/// Validates the (key, value) tag pairs, failing construction if any of them is malformed.
///
/// Mirrors `UploaderBuilder::build()`, which collects *every* rejection and returns
/// "Error initializing exporter, missing or bad configuration: <reasons>" rather than dropping
/// the bad tags: a typo in `DD_TAGS` otherwise yields profiles permanently missing that
/// dimension with nothing anywhere to explain why. All the reasons are reported at once, for the
/// same reason the C++ does it -- fixing a broken tag list one round-trip at a time is miserable.
fn build_tags(tags: Vec<(String, String)>) -> PyResult<Vec<Tag>> {
    let mut parsed = Vec::with_capacity(tags.len());
    let mut reasons = Vec::new();
    for (key, value) in tags {
        match Tag::new(&key, &value) {
            Ok(tag) => parsed.push(tag),
            Err(e) => reasons.push(format!("{key}: {e}")),
        }
    }
    if !reasons.is_empty() {
        return Err(PyValueError::new_err(format!(
            "Error initializing exporter, missing or bad configuration: {}",
            reasons.join(", ")
        )));
    }
    Ok(parsed)
}

/// Pilot PyO3 binding for the profiling upload/export path (see the production-readiness
/// review this crate's fork-safety fix follows: PYO3_PRODUCTION_READINESS_REVIEW.md, item 1,
/// in the dd-trace-py checkout's sibling `py-wrappers` workspace -- not yet committed into this
/// repo). Wraps libdd-profiling's safe
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
        // Validated before the output_filename short-circuit below, mirroring
        // UploaderBuilder::build(), which collects tag rejections before it decides anything
        // about where the profile ends up (the file-output decision lives downstream, in
        // `Uploader`). Otherwise the same malformed DD_TAGS entry would raise for an agent
        // upload but be quietly accepted whenever DD_PROFILING_OUTPUT_PPROF is set.
        let tags = build_tags(tags)?;

        if let Some(output_filename) = output_filename {
            return Ok(ProfileUploaderPy {
                inner: None,
                output_filename: Some(output_filename),
            });
        }

        let mut endpoint = endpoint_for_url(url)?;

        if let Some(timeout_ms) = timeout_ms {
            endpoint.timeout_ms = timeout_ms;
        }

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
    /// `buffer` is taken as an owned `Vec<u8>` (one copy, from `<bytes>result.buffer` in
    /// `_ddup.pyx`) rather than a zero-copy `PyBackedBytes`/`Bytes::from_owner` view: libdd-
    /// profiling's `EncodedProfile::buffer` field (what this gets moved into a few lines down)
    /// is itself a `Vec<u8>`, not a `Bytes`/`Cow`, so a zero-copy Python-side view would still
    /// need `.to_vec()`'d into an owned buffer to construct it -- there's no copy to eliminate
    /// here, only one to move around.
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
                let mut guard = UPLOAD_CANCEL.lock();
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
    // C++ *pre*-increments: `Uploader`'s constructor bumps ProfilerState::upload_seq
    // (uploader.cpp:28) before export_to_file reads it (uploader.cpp:54), so its first dump is
    // `<name>.<pid>.1`, not `.0`. fetch_add returns the pre-increment value, hence the `+ 1`.
    let seq = UPLOAD_SEQ.fetch_add(1, Ordering::Relaxed) + 1;
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

#[cfg(test)]
mod tests {
    use super::*;

    // Not a fork race: per docs/native-code-review.md §2's own guidance, a race-based fork
    // test is inherently flaky. This instead verifies the mechanics the fork hooks rely on --
    // that holding UPLOAD_CANCEL across "before_fork" and releasing it in each of the two
    // possible "after_fork" hooks always leaves the mutex lockable again afterwards. If either
    // hook forgot to release the lock (or double-released it, which parking_lot's
    // `force_unlock` would panic on if actually unlocked), this would deadlock or panic instead
    // of returning.
    // Both cases share the process-wide UPLOAD_CANCEL static, so they run as one test rather
    // than two `#[test]` fns -- cargo test runs tests in parallel within the same process by
    // default, and two independent tests mutating the same static would be racy against each
    // other for no real reason.
    #[test]
    fn fork_hooks_leave_upload_cancel_unlocked() {
        // Parent path: the lock is released as-is, whatever was in it stays.
        {
            let mut guard = UPLOAD_CANCEL.lock();
            *guard = Some(CancellationToken::new());
        }
        profile_uploader_before_fork();
        // The whole point of before_fork is to hold the lock across the fork boundary -- assert
        // that it actually is held here, so this test can't pass if before_fork silently no-ops
        // (e.g. a future refactor that forgets the `try_lock`/`forget` pair).
        assert!(
            UPLOAD_CANCEL.try_lock().is_none(),
            "before_fork should leave UPLOAD_CANCEL locked"
        );
        profile_uploader_after_fork_parent();
        // If the lock were still held, this would hang forever instead of returning.
        assert!(
            UPLOAD_CANCEL.try_lock().is_some(),
            "UPLOAD_CANCEL should be unlocked after after_fork_parent"
        );

        // Child path: the lock is released AND cleared -- no thread in the child could have a
        // real upload in flight, so whatever token was inherited is dropped, not cancelled.
        {
            let mut guard = UPLOAD_CANCEL.lock();
            *guard = Some(CancellationToken::new());
        }
        profile_uploader_before_fork();
        assert!(
            UPLOAD_CANCEL.try_lock().is_none(),
            "before_fork should leave UPLOAD_CANCEL locked"
        );
        profile_uploader_after_fork_child();
        let guard = UPLOAD_CANCEL.try_lock();
        assert!(
            guard.is_some(),
            "UPLOAD_CANCEL should be unlocked after after_fork_child"
        );
        assert!(
            guard.unwrap().is_none(),
            "after_fork_child should have cleared whatever token the parent inherited"
        );
    }

    // Spelled out here rather than pulled from the `hex` crate: `hex` is a transitive dependency
    // of libdatadog, not a direct one of this crate, and the endpoint tests below aren't worth a
    // new dev-dependency. `dead_code` is allowed because its only callers are the two
    // platform-gated tests below -- on a target that is neither unix nor windows, nothing uses
    // it.
    #[allow(dead_code)]
    fn hex_encode(s: &str) -> String {
        s.bytes().map(|b| format!("{b:02x}")).collect()
    }

    // A plain agent URL must survive untouched apart from config::agent's own path suffix --
    // parse_uri only special-cases the three path-carrying schemes.
    #[test]
    fn endpoint_for_url_keeps_http_urls_intact() {
        let endpoint = endpoint_for_url("http://localhost:8126").unwrap();
        assert_eq!(endpoint.url.scheme_str(), Some("http"));
        assert_eq!(endpoint.url.authority().unwrap(), "localhost:8126");
        assert_eq!(endpoint.url.path(), "/profiling/v1/input");
    }

    // The regression this guards: with a bare `url.parse::<http::Uri>()` the socket path landed
    // in the URI *path* and config::agent appended the intake route to it, producing
    // "/var/run/datadog/apm.socket/profiling/v1/input" and failing every upload. The authority
    // must instead be the hex-encoded socket path, byte-for-byte what the C++ path gets out of
    // ddog_prof_Endpoint_agent -> try_to_url -> socket_path_to_uri.
    #[cfg(unix)]
    #[test]
    fn endpoint_for_url_hex_encodes_unix_socket_path_in_authority() {
        use std::path::Path;

        let socket_path = "/var/run/datadog/apm.socket";
        let endpoint = endpoint_for_url(&format!("unix://{socket_path}")).unwrap();

        let expected =
            libdd_common::connector::uds::socket_path_to_uri(Path::new(socket_path)).unwrap();
        assert_eq!(endpoint.url.scheme_str(), Some("unix"));
        assert_eq!(endpoint.url.authority(), expected.authority());
        assert_eq!(endpoint.url.authority().unwrap(), &hex_encode(socket_path));
        // config::agent still appends the intake route to the (now empty) path, exactly as it
        // does for the C++ path -- the socket path is out of its way, in the authority.
        assert_eq!(endpoint.url.path(), "/profiling/v1/input");
    }

    #[cfg(windows)]
    #[test]
    fn endpoint_for_url_hex_encodes_named_pipe_path_in_authority() {
        let pipe_path = r"\\.\pipe\datadog";
        let endpoint = endpoint_for_url(&format!("windows:{pipe_path}")).unwrap();

        assert_eq!(endpoint.url.scheme_str(), Some("windows"));
        assert_eq!(endpoint.url.authority().unwrap(), &hex_encode(pipe_path));
        assert_eq!(endpoint.url.path(), "/profiling/v1/input");
    }

    // The trap the two branches exist to avoid: `file://` means libdatadog's debug
    // dump-the-HTTP-request-to-disk endpoint, whose URL keeps the path as a *path*. If this ever
    // starts coming back hex-encoded, someone has routed it through parse_uri and the file-dump
    // tests in tests/tracer/test_native_profile_uploader.py are silently doing something else.
    #[test]
    fn endpoint_for_url_keeps_file_scheme_on_the_file_dump_endpoint() {
        let path = "/tmp/profile_dump.http";
        let endpoint = endpoint_for_url(&format!("file://{path}")).unwrap();

        assert_eq!(endpoint.url.scheme_str(), Some("file"));
        assert!(endpoint.is_file_endpoint());
        // config::file round-trips through Endpoint::from_slice -> libdd_common::parse_uri, whose
        // `file://` arm also hex-encodes the path into the authority, leaving the path a bare "/".
        // So "same encoding as the agent arm" is NOT the thing that distinguishes these two
        // branches -- see the next assertion for the thing that does.
        assert_eq!(endpoint.url.authority().unwrap(), &hex_encode(path));
        // The load-bearing assertion: config::file leaves the path alone, while config::agent
        // appends the intake route. If someone collapses the two branches and sends `file://`
        // through parse_uri -> config::agent, this becomes "/profiling/v1/input" and every
        // file-dump test in tests/tracer/test_native_profile_uploader.py silently starts
        // exercising the agent path instead.
        assert_eq!(endpoint.url.path(), "/");
    }

    #[test]
    fn build_tags_accepts_well_formed_tags() {
        let tags = build_tags(vec![
            ("env".to_string(), "prod".to_string()),
            ("service".to_string(), "svc".to_string()),
        ])
        .unwrap();
        assert_eq!(tags.len(), 2);
        assert_eq!(tags[0].to_string(), "env:prod");
    }

    // Malformed tags must abort construction with every bad tag named, mirroring
    // UploaderBuilder::build() -- silently dropping them leaves profiles missing a dimension
    // with nothing to explain why.
    #[test]
    fn build_tags_rejects_malformed_tags_and_names_all_of_them() {
        let err = build_tags(vec![
            ("env".to_string(), "prod".to_string()),
            ("first-bad".to_string(), String::new()),
            ("second-bad".to_string(), String::new()),
        ])
        .unwrap_err();

        let message = err.to_string();
        assert!(
            message.contains("Error initializing exporter, missing or bad configuration: "),
            "unexpected message: {message}"
        );
        // Every rejection is named, not just the first one -- fixing a broken DD_TAGS one
        // round-trip at a time is exactly what the C++ builder set out to avoid.
        assert!(
            message.contains("first-bad"),
            "unexpected message: {message}"
        );
        assert!(
            message.contains("second-bad"),
            "unexpected message: {message}"
        );
    }

    // Guards the off-by-one against dd_wrapper: C++ pre-increments upload_seq, so the very first
    // dump of a process is `.1`, never `.0`. Asserts `>= 1` plus consecutiveness rather than an
    // exact number, because UPLOAD_SEQ is a process-wide static and cargo runs these tests in
    // parallel in one process (same caveat as the fork-hook test above).
    #[test]
    fn write_to_file_sequence_numbers_are_one_based_and_consecutive() {
        // Its own directory rather than a name in the shared temp dir: the seq is discovered by
        // listing the directory, and a stale file left by a crashed earlier run that happened to
        // reuse this pid would otherwise skew the `max()` below.
        let dir = std::env::temp_dir().join(format!(
            "ddtrace-seq-test-{}-{:?}",
            std::process::id(),
            std::thread::current().id()
        ));
        fs::create_dir_all(&dir).unwrap();
        let base = dir.join("dump");
        let base = base.to_str().unwrap();

        let seq_of = |base: &str| -> u64 {
            write_to_file(base, b"pprof", None, None).unwrap();
            let prefix = format!("{base}.{}.", std::process::id());
            // UPLOAD_SEQ is process-wide and other tests may have bumped it, so the seq this
            // call used isn't predictable -- find it as the highest one now on disk.
            fs::read_dir(&dir)
                .unwrap()
                .filter_map(|entry| {
                    let path = entry.ok()?.path();
                    let name = path.to_str()?.strip_prefix(&prefix)?;
                    name.strip_suffix(".pprof")?.parse::<u64>().ok()
                })
                .max()
                .expect("write_to_file produced no .pprof")
        };

        let first = seq_of(base);
        let second = seq_of(base);
        // No .info.json is written by these calls (info_json is None), which is what
        // test_output_filename_omits_info_json_when_absent asserts from the Python side.
        let _ = fs::remove_dir_all(&dir);

        assert!(first >= 1, "first dump must not be `.0`, got `.{first}`");
        assert_eq!(second, first + 1);
    }

    #[test]
    fn ns_to_systemtime_round_trips_positive_and_negative() {
        let positive = ns_to_systemtime(12_000_000_034).unwrap();
        assert_eq!(
            positive.duration_since(SystemTime::UNIX_EPOCH).unwrap(),
            Duration::from_nanos(12_000_000_034)
        );

        let negative = ns_to_systemtime(-5_000_000_000).unwrap();
        assert_eq!(
            SystemTime::UNIX_EPOCH.duration_since(negative).unwrap(),
            Duration::from_nanos(5_000_000_000)
        );
    }
}
