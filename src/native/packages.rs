//! Native replacement for the `importlib.metadata` walk behind
//! `ddtrace.internal.packages`: the distribution name/version map, the
//! importable-package map (`packages_distributions`) and the import root
//! mapping used to attribute files to distributions.
//!
//! `importlib.metadata` is general purpose: it builds a `PackagePath` per
//! RECORD line and, since Python 3.12, stats every one of them to drop files
//! that are missing on disk, and it parses the whole METADATA file with the
//! email parser just to read two headers. This module reads the same files but
//! stats a listed file only when it could contribute something new, and stops
//! parsing METADATA at the end of the header block.
//!
//! sys.path entries can be directories or zip archives (zipapps, PEX, zipped
//! eggs), which zipimport imports from just the same; both are handled here,
//! so the scan never has to run Python code.

use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;
use pyo3::types::PyModule;
use rustc_hash::{FxHashMap, FxHashSet};
use std::cell::RefCell;
use std::fmt::Display;
use std::fs::{self, File};
use std::io::Read;
use std::panic::{self, AssertUnwindSafe};
use std::path::{Component, Path, PathBuf};
use zip::ZipArchive;

/// (name, version, import root keys, top-level names) for one distribution.
type DistRecord = (String, Option<String>, Vec<String>, Vec<String>);
/// (metadata path, error message) for a distribution that could not be read.
type DistError = (String, String);

/// Decode a file the way PathDistribution.read_text does: text that is not
/// valid UTF-8 is an error for the whole distribution, as the
/// UnicodeDecodeError would be in Python. Line endings are left alone; split
/// with text_lines instead of translating whole files, as METADATA files with
/// Windows line endings can carry long descriptions that are never looked at.
fn decode(bytes: Vec<u8>, what: impl Display) -> Result<Option<String>, String> {
    String::from_utf8(bytes)
        .map(Some)
        .map_err(|e| format!("{what}: {e}"))
}

/// The lines of text as Python sees them after a text-mode read: split on
/// \n, \r\n and lone \r, without the line endings.
fn text_lines(text: &str) -> impl Iterator<Item = &str> {
    text.split_terminator('\n')
        .flat_map(|line| line.strip_suffix('\r').unwrap_or(line).split('\r'))
}

/// Where the files of a sys.path entry live. Paths inside it are given as
/// PurePosixPath-style parts relative to the entry.
enum Root {
    Dir(PathBuf),
    Zip {
        path: PathBuf,
        // ZipArchive reads need exclusive access.
        archive: RefCell<ZipArchive<File>>,
        names: FxHashSet<String>,
    },
}

impl Root {
    /// The root for a sys.path entry and its top-level children in listing
    /// order, as importlib's FastPath.children sees them. None when there is
    /// nothing to list: a missing entry, or a file that is not a zip archive.
    fn open(entry: &Path) -> Option<(Root, Vec<String>)> {
        let listing_dir = if entry.as_os_str().is_empty() {
            Path::new(".")
        } else {
            entry
        };
        if let Ok(listing) = fs::read_dir(listing_dir) {
            let children = listing
                .flatten()
                .filter_map(|e| e.file_name().to_str().map(str::to_string))
                .collect();
            return Some((Root::Dir(entry.to_path_buf()), children));
        }
        let archive = ZipArchive::new(File::open(entry).ok()?).ok()?;
        let names: FxHashSet<String> = archive.file_names().map(str::to_string).collect();
        let mut seen = FxHashSet::default();
        let children = archive
            .file_names()
            .filter_map(|name| name.split('/').next())
            .filter(|top| !top.is_empty() && seen.insert(top.to_string()))
            .map(str::to_string)
            .collect();
        Some((
            Root::Zip {
                path: entry.to_path_buf(),
                archive: RefCell::new(archive),
                names,
            },
            children,
        ))
    }

    fn dir_path(dir: &Path, parts: &[&str]) -> PathBuf {
        let mut path = dir.to_path_buf();
        path.extend(parts);
        path
    }

    /// Absent and unreadable files count as missing, as in read_text.
    fn read_text(&self, parts: &[&str]) -> Result<Option<String>, String> {
        match self {
            Root::Dir(dir) => {
                let path = Self::dir_path(dir, parts);
                match fs::read(&path) {
                    Ok(bytes) => decode(bytes, path.display()),
                    Err(_) => Ok(None),
                }
            }
            Root::Zip { archive, .. } => {
                let name = parts.join("/");
                let mut archive = archive.borrow_mut();
                // Only a missing member is absent. Anything else (bzip2 or lzma
                // compression, which this build cannot read, encryption,
                // corruption) is an error for the distribution, as importlib
                // would raise on it rather than skip it quietly.
                let mut file = match archive.by_name(&name) {
                    Ok(file) => file,
                    Err(zip::result::ZipError::FileNotFound) => return Ok(None),
                    Err(e) => return Err(format!("{}: {e}", self.display(parts))),
                };
                let mut bytes = Vec::new();
                file.read_to_end(&mut bytes)
                    .map_err(|e| format!("{}: {e}", self.display(parts)))?;
                decode(bytes, self.display(parts))
            }
        }
    }

    fn exists(&self, parts: &[&str]) -> bool {
        match self {
            Root::Dir(dir) => Self::dir_path(dir, parts).exists(),
            Root::Zip { names, .. } => names.contains(&parts.join("/")),
        }
    }

    /// The directory absolute paths can be made relative to; zip members have
    /// no absolute paths.
    fn base(&self) -> Option<&Path> {
        match self {
            Root::Dir(dir) => Some(dir),
            Root::Zip { .. } => None,
        }
    }

    fn display(&self, parts: &[&str]) -> String {
        match self {
            Root::Dir(dir) => Self::dir_path(dir, parts).display().to_string(),
            Root::Zip { path, .. } => format!("{}/{}", path.display(), parts.join("/")),
        }
    }
}

/// Python's `a or b` on read_text results: an empty file is as good as none.
fn non_empty(text: Option<String>) -> Option<String> {
    text.filter(|t| !t.is_empty())
}

/// The Name and Version headers, parsed the way the compat32 email parser does
/// for Message.get: first occurrence wins, continuation lines are folded in
/// verbatim, and the header block ends at the first blank or non-header line.
fn name_and_version(text: &str) -> (Option<String>, Option<String>) {
    let mut name = None;
    let mut version = None;
    let mut current: Option<(&str, String)> = None;

    let mut finish = |header: Option<(&str, String)>| {
        if let Some((key, value)) = header {
            if name.is_none() && key.eq_ignore_ascii_case("name") {
                name = Some(value);
            } else if version.is_none() && key.eq_ignore_ascii_case("version") {
                version = Some(value);
            }
        }
    };

    for line in text_lines(text) {
        if line.is_empty() {
            break;
        }
        if line.starts_with([' ', '\t']) {
            // compat32 keeps folded lines verbatim, joined by the (translated)
            // line ending.
            if let Some((_, value)) = current.as_mut() {
                value.push('\n');
                value.push_str(line);
            }
            continue;
        }
        if line.starts_with("From ") {
            continue;
        }
        let Some(colon) = line.find(':') else {
            break;
        };
        let key = &line[..colon];
        if !key.bytes().all(|b| (0x21..=0x7e).contains(&b)) {
            break;
        }
        finish(current.take());
        current = Some((
            key,
            line[colon + 1..]
                .trim_start_matches([' ', '\t'])
                .to_string(),
        ));
    }
    finish(current);

    (name, version)
}

/// First field of a RECORD line under the default csv dialect.
fn csv_first_field(line: &str) -> String {
    let Some(rest) = line.strip_prefix('"') else {
        return line.split(',').next().unwrap_or_default().to_string();
    };
    let mut field = String::new();
    let mut chars = rest.chars().peekable();
    while let Some(c) = chars.next() {
        if c != '"' {
            field.push(c);
        } else if chars.peek() == Some(&'"') {
            chars.next();
            field.push('"');
        } else {
            // csv keeps whatever follows the closing quote up to the delimiter.
            field.extend(chars.take_while(|&c| c != ','));
            break;
        }
    }
    field
}

/// installed-files.txt entries are relative to the .egg-info directory;
/// importlib rebases them onto the site directory. Entries that land outside
/// it would start with "..", which the caller discards, so they are dropped
/// here instead.
fn egg_info_relative(base: Option<&Path>, info_name: &str, entry: &str) -> Option<String> {
    let entry = Path::new(entry);
    let (mut parts, relative) = if entry.is_absolute() {
        (Vec::new(), entry.strip_prefix(base?).ok()?)
    } else {
        (vec![info_name.to_string()], entry)
    };
    for component in relative.components() {
        match component {
            Component::Normal(c) => parts.push(c.to_str()?.to_string()),
            Component::ParentDir => {
                parts.pop()?;
            }
            Component::CurDir => {}
            _ => return None,
        }
    }
    Some(parts.join("/"))
}

/// Distribution.files without the per-file existence filter, which the caller
/// applies lazily.
fn file_names(root: &Root, info: &str) -> Result<Option<Vec<String>>, String> {
    if let Some(text) = non_empty(root.read_text(&[info, "RECORD"])?) {
        return Ok(Some(text_lines(&text).map(csv_first_field).collect()));
    }
    if let Some(text) = non_empty(root.read_text(&[info, "installed-files.txt"])?) {
        return Ok(Some(
            text_lines(&text)
                .filter_map(|line| egg_info_relative(root.base(), info, line))
                .collect(),
        ));
    }
    if let Some(text) = non_empty(root.read_text(&[info, "SOURCES.txt"])?) {
        return Ok(Some(text_lines(&text).map(str::to_string).collect()));
    }
    Ok(None)
}

/// PurePosixPath(name).parts.
fn posix_parts(name: &str) -> Vec<&str> {
    let mut parts = Vec::new();
    let rest = if name.starts_with("//") && !name.starts_with("///") {
        parts.push("//");
        &name[2..]
    } else if name.starts_with('/') {
        parts.push("/");
        name.trim_start_matches('/')
    } else {
        name
    };
    parts.extend(rest.split('/').filter(|p| !p.is_empty() && *p != "."));
    parts
}

/// The deepest importable root for a file: the first directory level that is
/// a regular package. See _package_for_root_module_mapping for why namespace
/// levels cannot be used as keys.
///
/// Every distribution under a sys.path entry shares the same base, so the
/// relative prefix identifies the directory. Hashing that short string is far
/// cheaper than hashing a PathBuf, which goes component by component.
fn root_key(root: &Root, parts: &[&str], regular: &mut FxHashMap<String, bool>) -> String {
    let n = parts.len();
    if n < 2 {
        return parts[0].to_string();
    }
    let mut prefix = String::new();
    for (i, &part) in parts[..n - 1].iter().enumerate() {
        if i > 0 {
            prefix.push('/');
        }
        prefix.push_str(part);
        let is_regular = match regular.get(prefix.as_str()) {
            Some(&is_regular) => is_regular,
            None => {
                let mut init = parts[..=i].to_vec();
                init.push("__init__.py");
                let is_regular = root.exists(&init);
                regular.insert(prefix.clone(), is_regular);
                is_regular
            }
        };
        if is_regular {
            return prefix;
        }
    }
    // Every directory level is a namespace: keep the full path so sibling
    // modules from different distributions stay distinct.
    parts.join("/")
}

/// inspect.getmodulename for a file name, given the import suffixes longest
/// first.
fn module_name<'a>(file_name: &'a str, suffixes: &[String]) -> Option<&'a str> {
    suffixes
        .iter()
        .find_map(|suffix| file_name.strip_suffix(suffix.as_str()))
}

/// Read one distribution. Errors that only affect its file list are pushed to
/// errors and the distribution is still returned, so that its name and version
/// stay available.
fn scan_distribution(
    root: &Root,
    info: &str,
    suffixes: &[String],
    regular: &mut FxHashMap<String, bool>,
    errors: &mut Vec<String>,
) -> Result<Option<DistRecord>, String> {
    let text = match non_empty(root.read_text(&[info, "METADATA"])?) {
        Some(text) => Some(text),
        None => match non_empty(root.read_text(&[info, "PKG-INFO"])?) {
            Some(text) => Some(text),
            // An egg-info file rather than a directory.
            None => root.read_text(&[info])?,
        },
    };
    let Some(text) = text else {
        return Ok(None);
    };
    let (Some(name), version) = name_and_version(&text) else {
        return Ok(None);
    };
    if name.is_empty() {
        return Ok(None);
    }
    let version = version.filter(|v| !v.is_empty());

    let mut file_errors = |result: Result<Option<String>, String>| {
        result.unwrap_or_else(|e| {
            errors.push(e);
            None
        })
    };
    let declared: Vec<String> = file_errors(root.read_text(&[info, "top_level.txt"]))
        .map(|text| text.split_whitespace().map(str::to_string).collect())
        .unwrap_or_default();
    let names = match file_names(root, info) {
        Ok(names) => names.unwrap_or_default(),
        Err(e) => {
            errors.push(e);
            Vec::new()
        }
    };
    // Root keys only matter for distributions with a version, and inferred
    // top-level names only when top_level.txt declares none.
    let want_keys = version.is_some();
    let want_top_level = declared.is_empty();

    let mut keys = Vec::new();
    let mut seen_keys = FxHashSet::default();
    let mut top_level = Vec::new();
    let mut seen_top_level = FxHashSet::default();
    for name in &names {
        let parts = posix_parts(name);
        let Some(&first) = parts.first() else {
            continue;
        };
        // Metadata directories and files outside the site directory never
        // yield a key, and their top-level names are not importable.
        if first.ends_with(".dist-info") || first.ends_with(".egg-info") || first == ".." {
            continue;
        }
        let key = want_keys
            .then(|| root_key(root, &parts, regular))
            .filter(|key| !seen_keys.contains(key));
        let candidate = want_top_level
            .then(|| {
                if parts.len() > 1 {
                    first
                } else {
                    module_name(first, suffixes)
                        .filter(|m| !m.is_empty())
                        .unwrap_or(first)
                }
            })
            .filter(|c| !c.contains('.') && !seen_top_level.contains(*c));
        if key.is_none() && candidate.is_none() {
            continue;
        }
        // importlib drops listed files that are missing on disk, so only an
        // existing file may contribute. One hit per key or name is enough.
        // The file is often the package's __init__.py, whose existence the
        // regular package check has already established.
        let known = parts.len() > 1
            && parts.last() == Some(&"__init__.py")
            && regular.get(parts[..parts.len() - 1].join("/").as_str()) == Some(&true);
        if !known && !root.exists(&parts) {
            continue;
        }
        if let Some(key) = key {
            seen_keys.insert(key.clone());
            keys.push(key);
        }
        if let Some(candidate) = candidate {
            seen_top_level.insert(candidate.to_string());
            top_level.push(candidate.to_string());
        }
    }

    Ok(Some((
        name,
        version,
        keys,
        if want_top_level { top_level } else { declared },
    )))
}

/// PEP 503 normalisation with dashes as underscores (Prepared.normalize), for
/// a name that is already lowercase.
fn normalize(name: &str) -> String {
    let mut out = String::with_capacity(name.len());
    let mut in_separator = false;
    for c in name.chars() {
        if matches!(c, '-' | '_' | '.') {
            if !in_separator {
                out.push('_');
            }
            in_separator = true;
        } else {
            out.push(c);
            in_separator = false;
        }
    }
    out
}

/// The metadata directories among a sys.path entry's children, in the order
/// importlib.metadata's Lookup yields them: grouped by normalised name, groups
/// in order of first appearance in the listing, then the EGG-INFO of a legacy
/// .egg entry.
fn metadata_dirs(entry: &Path, children: Vec<String>) -> Vec<String> {
    // os.path.basename: an entry with a trailing separator has an empty base,
    // where Path::file_name would skip past the separator.
    let base_is_egg = entry
        .to_str()
        .and_then(|e| e.rsplit(std::path::is_separator).next())
        .is_some_and(|base| base.to_lowercase().ends_with(".egg"));
    let mut groups: Vec<Vec<String>> = Vec::new();
    let mut index: FxHashMap<String, usize> = FxHashMap::default();
    let mut eggs = Vec::new();
    for child in children {
        let low = child.to_lowercase();
        if low.ends_with(".dist-info") || low.ends_with(".egg-info") {
            let stem = low.rsplit_once('.').map_or("", |(stem, _)| stem);
            let name = stem.split('-').next().unwrap_or_default();
            let slot = *index.entry(normalize(name)).or_insert_with(|| {
                groups.push(Vec::new());
                groups.len() - 1
            });
            groups[slot].push(child);
        } else if base_is_egg && low == "egg-info" {
            eggs.push(child);
        }
    }
    groups.into_iter().flatten().chain(eggs).collect()
}

// The scan runs on a single thread. It used to fan out over a few scoped
// worker threads, which made it about 2x faster with a warm file system cache
// and up to about 3.5x with a cold one, but the boot-time scan now runs in the
// background, so nothing waits for it unless it is read, or a fork happens,
// within its first few hundred milliseconds. To reintroduce the workers:
//
// - Only ever use them from the boot-time prefetch thread, which ddtrace joins
//   before every fork; lazy scans run on application threads, which can fork
//   at any time.
// - Spawn them with std::thread::Builder::spawn_scoped and carry on with
//   whatever could be started, the calling thread included: Scope::spawn
//   panics when the OS refuses a thread (pids limit, RLIMIT_NPROC), and that
//   would happen at interpreter startup.
// - Have them pull distributions off a shared atomic index, each with its own
//   regular-package cache, and sort the results back into discovery order.
// - Re-raise a worker's panic on join rather than dropping its results; the
//   catch_unwind in scan_distributions turns it into a RuntimeError.
// - Put Root::Zip's archive back behind a Mutex: ZipArchive reads need
//   exclusive access.
fn scan(entry: &Path, suffixes: &[String]) -> (Vec<DistRecord>, Vec<DistError>) {
    let Some((root, children)) = Root::open(entry) else {
        return (Vec::new(), Vec::new());
    };
    let mut regular = FxHashMap::default();
    let mut dists = Vec::new();
    let mut errors = Vec::new();
    for info in metadata_dirs(entry, children) {
        let mut dist_errors = Vec::new();
        match scan_distribution(&root, &info, suffixes, &mut regular, &mut dist_errors) {
            Ok(record) => dists.extend(record),
            Err(error) => dist_errors.push(error),
        }
        let path = root.display(&[&info]);
        errors.extend(dist_errors.into_iter().map(|e| (path.clone(), e)));
    }
    (dists, errors)
}

/// Whether the interpreter is finalizing. Reads an atomic flag, so it is safe
/// to call without the GIL.
#[cfg(Py_3_13)]
fn is_finalizing() -> bool {
    // SAFETY: no arguments, no GIL needed.
    unsafe { pyo3::ffi::Py_IsFinalizing() != 0 }
}

#[cfg(not(Py_3_13))]
fn is_finalizing() -> bool {
    extern "C" {
        // Exported by CPython up to 3.12; Py_IsFinalizing replaced it in 3.13.
        fn _Py_IsFinalizing() -> std::os::raw::c_int;
    }
    // SAFETY: no arguments, no GIL needed.
    unsafe { _Py_IsFinalizing() != 0 }
}

/// Scan one sys.path entry for installed distributions.
///
/// Returns `(dists, errors)`. Each dist is `(name, version, keys, top_level)`:
/// version is `None` when missing, keys are the import roots the distribution
/// ships (only computed when it has a version), and top_level are the names
/// packages_distributions would map to it. Each error is
/// `(metadata_path, message)` for a file that could not be decoded. Entries
/// that cannot be listed (missing, or files that are not zip archives) have no
/// distributions, as for importlib.
///
/// module_suffixes are importlib.machinery.all_suffixes(), longest first; they
/// are what inspect.getmodulename strips to infer top-level module names.
///
/// A panic in the scan is raised as RuntimeError rather than PyO3's
/// PanicException: the scan runs at interpreter startup, where a
/// BaseException would get past every handler and abort the process.
#[pyfunction]
fn scan_distributions(
    py: Python<'_>,
    entry: PathBuf,
    module_suffixes: Vec<String>,
) -> PyResult<(Vec<DistRecord>, Vec<DistError>)> {
    py.detach(move || {
        let result = panic::catch_unwind(AssertUnwindSafe(|| scan(&entry, &module_suffixes)));
        if is_finalizing() {
            // Re-acquiring the GIL now would kill the thread (CPython up to
            // 3.13.7, by unwinding through these frames) or hang it. Only
            // daemon threads can get here, as non-daemon ones are joined
            // before finalization starts, and the process is exiting: stay
            // off the GIL for good.
            loop {
                std::thread::park();
            }
        }
        result
    })
    .map_err(|payload| {
        let reason = payload
            .downcast_ref::<&str>()
            .map(|s| s.to_string())
            .or_else(|| payload.downcast_ref::<String>().cloned())
            .unwrap_or_default();
        PyRuntimeError::new_err(format!("distribution scan failed: {reason}"))
    })
}

pub fn register_packages(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(scan_distributions, m)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn metadata_headers() {
        let text = "Metadata-Version: 2.1\nName: foo\nVersion: 1.0\nName: bar\n\nVersion: 2.0\n";
        assert_eq!(
            name_and_version(text),
            (Some("foo".to_string()), Some("1.0".to_string()))
        );
        // Header block ends at the first non-header line.
        assert_eq!(
            name_and_version("Name: foo\nnot a header\nVersion: 1\n"),
            (Some("foo".to_string()), None)
        );
        // Folded continuation lines are kept verbatim, like compat32 does
        // (read_text has already translated line endings).
        assert_eq!(
            name_and_version("Name: foo\n bar\nVersion: 1\n").0,
            Some("foo\n bar".to_string())
        );
        // A misplaced envelope line is skipped, not the end of the headers.
        assert_eq!(
            name_and_version("Name: foo\nFrom someone\nVersion: 1\n"),
            (Some("foo".to_string()), Some("1".to_string()))
        );
    }

    #[test]
    fn record_fields() {
        assert_eq!(csv_first_field("foo/bar.py,sha256=x,1"), "foo/bar.py");
        assert_eq!(csv_first_field("\"a,b\"\"c.py\",sha256=x,1"), "a,b\"c.py");
        assert_eq!(csv_first_field("foo.py"), "foo.py");
    }

    #[test]
    fn parts() {
        assert_eq!(posix_parts("a//b/./c.py"), vec!["a", "b", "c.py"]);
        assert_eq!(posix_parts("/usr/bin/x"), vec!["/", "usr", "bin", "x"]);
        assert_eq!(posix_parts("//x"), vec!["//", "x"]);
        assert!(posix_parts("").is_empty());
    }

    #[test]
    fn egg_info_entries() {
        let base = Some(Path::new("/site"));
        assert_eq!(
            egg_info_relative(base, "foo.egg-info", "../foo/__init__.py").as_deref(),
            Some("foo/__init__.py")
        );
        assert_eq!(
            egg_info_relative(base, "foo.egg-info", "../../bin/foo"),
            None
        );
        assert_eq!(
            egg_info_relative(base, "foo.egg-info", "PKG-INFO").as_deref(),
            Some("foo.egg-info/PKG-INFO")
        );
        // Absolute entries count when they are inside the site directory.
        assert_eq!(
            egg_info_relative(base, "foo.egg-info", "/site/foo/core.py").as_deref(),
            Some("foo/core.py")
        );
        assert_eq!(
            egg_info_relative(base, "foo.egg-info", "/usr/bin/foo"),
            None
        );
    }

    #[test]
    fn text_mode_line_endings() {
        assert_eq!(
            text_lines("a\r\nb\rc\nd\n").collect::<Vec<_>>(),
            vec!["a", "b", "c", "d"]
        );
        assert_eq!(text_lines("a\n\nb").collect::<Vec<_>>(), vec!["a", "", "b"]);
        // Windows line endings, including a folded header and a blank line
        // ending the header block.
        assert_eq!(
            name_and_version("Name: foo\r\n bar\r\nVersion: 1 \r\n\r\nVersion: 2\r\n"),
            (Some("foo\n bar".to_string()), Some("1 ".to_string()))
        );
        assert_eq!(
            name_and_version("Name: foo\rVersion: 1\r"),
            (Some("foo".to_string()), Some("1".to_string()))
        );
    }

    #[test]
    fn module_names() {
        let suffixes = vec![
            ".cpython-313-darwin.so".to_string(),
            ".abi3.so".to_string(),
            ".pyc".to_string(),
            ".py".to_string(),
            ".so".to_string(),
        ];
        assert_eq!(module_name("six.py", &suffixes), Some("six"));
        assert_eq!(
            module_name("_x.cpython-313-darwin.so", &suffixes),
            Some("_x")
        );
        assert_eq!(module_name("foo.pth", &suffixes), None);
        // getmodulename(".py") is "", which callers treat as no module name.
        assert_eq!(module_name(".py", &suffixes), Some(""));
    }

    fn temp_dir(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("ddtrace-pkg-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn zip_archive_entries() {
        use std::io::Write;
        use zip::write::SimpleFileOptions;

        let dir = temp_dir("zip");
        let archive = dir.join("deps.zip");
        let mut zip = zip::ZipWriter::new(File::create(&archive).unwrap());
        let deflated =
            SimpleFileOptions::default().compression_method(zip::CompressionMethod::Deflated);
        for (name, content) in [
            ("zp/__init__.py", ""),
            ("zp/core.py", ""),
            ("zp-1.0.dist-info/METADATA", "Name: zp\r\nVersion: 1.0\r\n"),
            (
                "zp-1.0.dist-info/RECORD",
                "zp/__init__.py,,\nzp/core.py,,\nzp/gone.py,,\nzp-1.0.dist-info/RECORD,,\n",
            ),
            ("solo.py", ""),
            ("solo-2.0.dist-info/METADATA", "Name: solo\nVersion: 2.0\n"),
            ("solo-2.0.dist-info/RECORD", "solo.py,,\nmissing.py,,\n"),
        ] {
            zip.start_file(name, deflated).unwrap();
            zip.write_all(content.as_bytes()).unwrap();
        }
        zip.finish().unwrap();

        let (dists, errors) = scan(&archive, &[".py".to_string()]);
        fs::remove_dir_all(&dir).unwrap();
        assert!(errors.is_empty(), "{errors:?}");
        assert_eq!(
            dists,
            vec![
                (
                    "zp".to_string(),
                    Some("1.0".to_string()),
                    vec!["zp".to_string()],
                    vec!["zp".to_string()]
                ),
                (
                    "solo".to_string(),
                    Some("2.0".to_string()),
                    vec!["solo.py".to_string()],
                    vec!["solo".to_string()]
                ),
            ]
        );
    }

    #[test]
    fn unlistable_entries_have_no_distributions() {
        let dir = temp_dir("unlistable");
        let not_a_zip = dir.join("notes.txt");
        fs::write(&not_a_zip, "hello").unwrap();
        assert!(scan(&not_a_zip, &[]).0.is_empty());
        assert!(scan(&dir.join("missing"), &[]).0.is_empty());
        fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn egg_directory_entries() {
        let dir = temp_dir("egg");
        let egg = dir.join("legacy-1.0-py3.13.egg");
        fs::create_dir_all(egg.join("EGG-INFO")).unwrap();
        fs::create_dir_all(egg.join("legacy")).unwrap();
        fs::write(egg.join("legacy/__init__.py"), "").unwrap();
        fs::write(
            egg.join("EGG-INFO/PKG-INFO"),
            "Name: legacy\nVersion: 1.0\n",
        )
        .unwrap();
        fs::write(
            egg.join("EGG-INFO/SOURCES.txt"),
            "setup.py\nlegacy/__init__.py\n",
        )
        .unwrap();
        fs::write(egg.join("EGG-INFO/top_level.txt"), "legacy\n").unwrap();

        let (dists, errors) = scan(&egg, &[".py".to_string()]);
        fs::remove_dir_all(&dir).unwrap();
        assert!(errors.is_empty(), "{errors:?}");
        assert_eq!(
            dists,
            vec![(
                "legacy".to_string(),
                Some("1.0".to_string()),
                vec!["legacy".to_string()],
                vec!["legacy".to_string()]
            )]
        );
    }

    #[test]
    fn normalized_names() {
        assert_eq!(normalize("zope.interface"), "zope_interface");
        assert_eq!(normalize("a-_.b"), "a_b");
    }
}
