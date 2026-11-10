//! Scan of installed distributions for `ddtrace.internal.packages`.
//!
//! Reads the same files as `importlib.metadata`, but stats a listed file only
//! when it could contribute something and parses METADATA headers only.
//! Handles directory and zip `sys.path` entries, so it never runs Python code.

use pyo3::exceptions::{PyNotImplementedError, PyRuntimeError, PyValueError};
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
/// (index into the records, metadata directory) for a distribution to be read
/// in Python, in place at that index.
type DistFallback = (usize, String);
type ScanResult = (Vec<DistRecord>, Vec<DistError>, Vec<DistFallback>);

/// Why a file could not be read.
enum ReadError {
    /// A zip member that Python's zipfile may still read (a compression method
    /// the crate is built without, for one): the caller reads the whole
    /// distribution in Python instead.
    Unsupported(String),
    /// Anything else, which Python could not read either.
    Failed(String),
}

impl From<String> for ReadError {
    fn from(message: String) -> Self {
        ReadError::Failed(message)
    }
}

/// Decode like PathDistribution.read_text: invalid UTF-8 fails the
/// distribution. Line endings are left alone; see text_lines.
fn decode(bytes: Vec<u8>, what: impl Display) -> Result<Option<String>, ReadError> {
    String::from_utf8(bytes)
        .map(Some)
        .map_err(|e| ReadError::Failed(format!("{what}: {e}")))
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
    fn listing_dir(entry: &Path) -> &Path {
        if entry.as_os_str().is_empty() {
            Path::new(".")
        } else {
            entry
        }
    }

    fn zip(entry: &Path) -> Option<Root> {
        let archive = ZipArchive::new(File::open(entry).ok()?).ok()?;
        let names = archive.file_names().map(str::to_string).collect();
        Some(Root::Zip {
            path: entry.to_path_buf(),
            archive: RefCell::new(archive),
            names,
        })
    }

    /// The root and its top-level children in listing order, like importlib's
    /// FastPath.children; None if there is nothing to list.
    fn open(entry: &Path) -> Option<(Root, Vec<String>)> {
        if let Ok(listing) = fs::read_dir(Self::listing_dir(entry)) {
            let children = listing
                .flatten()
                .filter_map(|e| e.file_name().to_str().map(str::to_string))
                .collect();
            return Some((Root::Dir(entry.to_path_buf()), children));
        }
        let root = Self::zip(entry)?;
        let Root::Zip { archive, .. } = &root else {
            unreachable!()
        };
        let mut seen = FxHashSet::default();
        let children = archive
            .borrow()
            .file_names()
            .filter_map(|name| name.split('/').next())
            .filter(|top| !top.is_empty() && seen.insert(top.to_string()))
            .map(str::to_string)
            .collect();
        Some((root, children))
    }

    /// The root without listing it, for reading files at known paths.
    fn at(entry: &Path) -> Option<Root> {
        if Self::listing_dir(entry).is_dir() {
            Some(Root::Dir(entry.to_path_buf()))
        } else {
            Self::zip(entry)
        }
    }

    fn dir_path(dir: &Path, parts: &[&str]) -> PathBuf {
        let mut path = dir.to_path_buf();
        path.extend(parts);
        path
    }

    /// Absent and unreadable files count as missing, as in read_text.
    fn read_text(&self, parts: &[&str]) -> Result<Option<String>, ReadError> {
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
                // Only a missing member is absent. Unsupported compression or
                // encryption hands the distribution to zipfile; anything else
                // (corruption) fails it.
                let mut file = match archive.by_name(&name) {
                    Ok(file) => file,
                    Err(zip::result::ZipError::FileNotFound) => return Ok(None),
                    Err(e @ zip::result::ZipError::UnsupportedArchive(_)) => {
                        return Err(ReadError::Unsupported(format!(
                            "{}: {e}",
                            self.display(parts)
                        )))
                    }
                    Err(e) => return Err(format!("{}: {e}", self.display(parts)).into()),
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
            // compat32 keeps folded lines verbatim.
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

/// An installed-files.txt entry, rebased from the .egg-info directory onto the
/// site directory; None if it lands outside it.
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
fn file_names(root: &Root, info: &str) -> Result<Option<Vec<String>>, ReadError> {
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

/// The deepest importable root for a file: its first regular-package level.
/// Cached by relative prefix, which is much cheaper to hash than a PathBuf.
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

/// The Name and Version headers of a distribution, either of which can be
/// missing, as importlib reads them for metadata["Name"] and version.
fn read_headers(root: &Root, info: &str) -> Result<(Option<String>, Option<String>), ReadError> {
    let text = match non_empty(root.read_text(&[info, "METADATA"])?) {
        Some(text) => Some(text),
        None => match non_empty(root.read_text(&[info, "PKG-INFO"])?) {
            Some(text) => Some(text),
            // An egg-info file rather than a directory.
            None => root.read_text(&[info])?,
        },
    };
    let (name, version) = text.map_or((None, None), |text| name_and_version(&text));
    Ok((
        name.filter(|n| !n.is_empty()),
        version.filter(|v| !v.is_empty()),
    ))
}

/// The name and version of a distribution, None if it has no name.
fn read_name_and_version(
    root: &Root,
    info: &str,
) -> Result<Option<(String, Option<String>)>, ReadError> {
    Ok(match read_headers(root, info)? {
        (Some(name), version) => Some((name, version)),
        (None, _) => None,
    })
}

/// A file-list result with failures moved to errors: they must not cost the
/// name and version. Unsupported files still fail the distribution, which
/// Python then reads in full.
fn soft<T>(
    result: Result<Option<T>, ReadError>,
    errors: &mut Vec<String>,
) -> Result<Option<T>, ReadError> {
    match result {
        Err(ReadError::Failed(e)) => {
            errors.push(e);
            Ok(None)
        }
        other => other,
    }
}

/// Read one distribution. File-list errors go to errors without losing the
/// name and version.
fn scan_distribution(
    root: &Root,
    info: &str,
    suffixes: &[String],
    regular: &mut FxHashMap<String, bool>,
    errors: &mut Vec<String>,
) -> Result<Option<DistRecord>, ReadError> {
    let Some((name, version)) = read_name_and_version(root, info)? else {
        return Ok(None);
    };

    let declared: Vec<String> = soft(root.read_text(&[info, "top_level.txt"]), errors)?
        .map(|text| text.split_whitespace().map(str::to_string).collect())
        .unwrap_or_default();
    let names = soft(file_names(root, info), errors)?.unwrap_or_default();
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
        // Only existing files count, as in importlib; one hit per key or name
        // is enough, and a regular package's __init__.py is known to exist.
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

/// The normalised distribution name in a metadata file name like
/// {name}-{version}.dist-info, lowercase.
fn metadata_name(low: &str) -> String {
    let stem = low.rsplit_once('.').map_or("", |(stem, _)| stem);
    normalize(stem.split('-').next().unwrap_or_default())
}

/// Metadata directories in importlib's Lookup order, each with the normalised
/// name it is looked up by: grouped by name in listing order, then a .egg
/// entry's EGG-INFO, named after the egg.
fn metadata_dirs(entry: &Path, children: Vec<String>) -> Vec<(String, String)> {
    // os.path.basename: an entry with a trailing separator has an empty base,
    // where Path::file_name would skip past the separator.
    let egg_name = entry
        .to_str()
        .and_then(|e| e.rsplit(std::path::is_separator).next())
        .map(str::to_lowercase)
        .filter(|base| base.ends_with(".egg"))
        .map(|base| metadata_name(&base));
    let mut groups: Vec<(String, Vec<String>)> = Vec::new();
    let mut index: FxHashMap<String, usize> = FxHashMap::default();
    let mut eggs = Vec::new();
    for child in children {
        let low = child.to_lowercase();
        if low.ends_with(".dist-info") || low.ends_with(".egg-info") {
            let name = metadata_name(&low);
            let slot = *index.entry(name.clone()).or_insert_with(|| {
                groups.push((name, Vec::new()));
                groups.len() - 1
            });
            groups[slot].1.push(child);
        } else if low == "egg-info" {
            if let Some(name) = &egg_name {
                eggs.push((name.clone(), child));
            }
        }
    }
    groups
        .into_iter()
        .flat_map(|(name, infos)| infos.into_iter().map(move |info| (name.clone(), info)))
        .chain(eggs)
        .collect()
}

// Possible improvement: scan distributions on a few scoped worker threads,
// about 2x faster with a warm file system cache and up to 3.5x with a cold one.
// The workers would need to:
// - run only in the boot-time prefetch thread, which forks join;
// - be spawned with Builder::spawn_scoped, tolerating spawn failures;
// - pull from a shared index, each with its own regular-package cache, and
//   have their results sorted back into discovery order;
// - have their panics re-raised on join;
// and Root::Zip's archive would need to go behind a Mutex.
fn scan(entry: &Path, suffixes: &[String]) -> ScanResult {
    let Some((root, children)) = Root::open(entry) else {
        return (Vec::new(), Vec::new(), Vec::new());
    };
    let mut regular = FxHashMap::default();
    let mut dists = Vec::new();
    let mut errors = Vec::new();
    let mut fallbacks = Vec::new();
    for (_, info) in metadata_dirs(entry, children) {
        let mut dist_errors = Vec::new();
        match scan_distribution(&root, &info, suffixes, &mut regular, &mut dist_errors) {
            Ok(record) => dists.extend(record),
            Err(ReadError::Failed(error)) => dist_errors.push(error),
            Err(ReadError::Unsupported(_)) => {
                // Python reads it all again: its own errors would be duplicates.
                fallbacks.push((dists.len(), info));
                continue;
            }
        }
        let path = root.display(&[&info]);
        errors.extend(dist_errors.into_iter().map(|e| (path.clone(), e)));
    }
    (dists, errors, fallbacks)
}

/// A panic caught at the boundary, as a RuntimeError.
fn panic_error(payload: Box<dyn std::any::Any + Send>, what: &str) -> PyErr {
    let reason = payload
        .downcast_ref::<&str>()
        .map(|s| s.to_string())
        .or_else(|| payload.downcast_ref::<String>().cloned())
        .unwrap_or_default();
    PyRuntimeError::new_err(format!("{what} failed: {reason}"))
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

/// Scan one sys.path entry, a directory or a zip archive.
///
/// Returns `(dists, errors, fallbacks)`: dists are `(name, version, keys,
/// top_level)`, errors `(metadata_path, message)`, and fallbacks
/// `(index, directory)` for distributions with zip members the native reader
/// cannot decode, to be read in Python and inserted at index. module_suffixes are
/// importlib.machinery.all_suffixes(), longest first. Panics are raised as
/// RuntimeError: a PanicException at startup would get past every handler.
///
/// The GIL is only released with release_gil, which only threads that cannot
/// outlive interpreter shutdown may ask for. A daemon thread re-acquiring it
/// once finalization has started is killed with pthread_exit (CPython up to
/// 3.13.7), and that cannot unwind through these frames: the finalization
/// check below narrows the window but cannot close it.
#[pyfunction]
#[pyo3(signature = (entry, module_suffixes, release_gil=false))]
fn scan_distributions(
    py: Python<'_>,
    entry: PathBuf,
    module_suffixes: Vec<String>,
    release_gil: bool,
) -> PyResult<ScanResult> {
    let result = if release_gil {
        py.detach(move || {
            let result = panic::catch_unwind(AssertUnwindSafe(|| scan(&entry, &module_suffixes)));
            if is_finalizing() {
                // Re-acquiring the GIL now would kill (CPython <= 3.13.7) or
                // hang the thread: stay off it.
                loop {
                    std::thread::park();
                }
            }
            result
        })
    } else {
        panic::catch_unwind(AssertUnwindSafe(|| scan(&entry, &module_suffixes)))
    };
    result.map_err(|payload| panic_error(payload, "distribution scan"))
}

/// The metadata directories under one sys.path entry, a directory or a zip
/// archive, as `(normalized_name, directory)` in importlib's lookup order.
/// Names are normalised like importlib's Prepared.normalize, with dashes as
/// underscores. Nothing is read but the listing, so callers can cache it on
/// the entry's mtime and read only the matches with read_distribution.
#[pyfunction]
fn list_distributions(entry: PathBuf) -> PyResult<Vec<(String, String)>> {
    panic::catch_unwind(AssertUnwindSafe(|| {
        Root::open(&entry)
            .map(|(_, children)| metadata_dirs(&entry, children))
            .unwrap_or_default()
    }))
    .map_err(|payload| panic_error(payload, "distribution listing"))
}

/// The `(name, version)` headers of the distribution in one metadata directory
/// listed by list_distributions, either of which can be None as with
/// importlib; None if the directory is gone. Raises ValueError if its metadata
/// cannot be decoded, and NotImplementedError if it is a zip member the native
/// reader cannot decode but Python's zipfile may.
#[pyfunction]
fn read_distribution(
    entry: PathBuf,
    directory: &str,
) -> PyResult<Option<(Option<String>, Option<String>)>> {
    panic::catch_unwind(AssertUnwindSafe(|| match Root::at(&entry) {
        // Removed since the listing: a zip that changed is listed again anyway,
        // by its mtime.
        Some(root @ Root::Dir(_)) if !root.exists(&[directory]) => Ok(None),
        Some(root) => read_headers(&root, directory).map(Some),
        None => Ok(None),
    }))
    .map_err(|payload| panic_error(payload, "distribution read"))?
    .map_err(|e| match e {
        ReadError::Unsupported(message) => PyNotImplementedError::new_err(message),
        ReadError::Failed(message) => PyValueError::new_err(message),
    })
}

pub fn register_packages(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(scan_distributions, m)?)?;
    m.add_function(wrap_pyfunction!(list_distributions, m)?)?;
    m.add_function(wrap_pyfunction!(read_distribution, m)?)?;
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

        let (dists, errors, fallbacks) = scan(&archive, &[".py".to_string()]);
        fs::remove_dir_all(&dir).unwrap();
        assert!(errors.is_empty(), "{errors:?}");
        assert!(fallbacks.is_empty(), "{fallbacks:?}");
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

        let (dists, errors, fallbacks) = scan(&egg, &[".py".to_string()]);
        fs::remove_dir_all(&dir).unwrap();
        assert!(errors.is_empty(), "{errors:?}");
        assert!(fallbacks.is_empty(), "{fallbacks:?}");
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

    /// The headers of the first readable distribution called name, the way the
    /// Python side looks it up from the listing.
    fn find(entry: &Path, name: &str) -> Option<(Option<String>, Option<String>)> {
        let wanted = normalize(&name.to_lowercase());
        let (_, children) = Root::open(entry)?;
        let root = Root::at(entry)?;
        metadata_dirs(entry, children)
            .into_iter()
            .filter(|(dist_name, _)| *dist_name == wanted)
            .find_map(|(_, info)| read_headers(&root, &info).ok())
    }

    fn found(name: &str, version: &str) -> Option<(Option<String>, Option<String>)> {
        Some((Some(name.to_string()), Some(version.to_string())))
    }

    #[test]
    fn find_by_name() {
        let dir = temp_dir("find");
        for (info, metadata) in [
            (
                "Zope.Interface-6.0.dist-info",
                "Name: zope.interface\nVersion: 6.0\n",
            ),
            ("broken-1.0.dist-info", "Version: 1.0\n"),
            ("legacy_pkg.egg-info", "Name: legacy-pkg\nVersion: 0.1\n"),
        ] {
            fs::create_dir_all(dir.join(info)).unwrap();
            fs::write(dir.join(info).join("METADATA"), metadata).unwrap();
        }
        assert_eq!(find(&dir, "zope-interface"), found("zope.interface", "6.0"));
        assert_eq!(find(&dir, "Legacy.Pkg"), found("legacy-pkg", "0.1"));
        // A match without a name still counts, as for importlib.
        assert_eq!(find(&dir, "broken"), Some((None, Some("1.0".to_string()))));
        assert_eq!(find(&dir, "missing"), None);
        assert_eq!(find(&dir.join("nowhere"), "zope.interface"), None);
        fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn find_egg_directory_entry() {
        let dir = temp_dir("find-egg");
        let egg = dir.join("Legacy_Egg-1.0-py3.13.egg");
        fs::create_dir_all(egg.join("EGG-INFO")).unwrap();
        fs::write(
            egg.join("EGG-INFO/PKG-INFO"),
            "Name: legacy-egg\nVersion: 1.0\n",
        )
        .unwrap();
        let result = find(&egg, "legacy-egg");
        fs::remove_dir_all(&dir).unwrap();
        assert_eq!(result, found("legacy-egg", "1.0"));
    }

    #[test]
    fn find_in_zip_archive() {
        use std::io::Write;

        let dir = temp_dir("find-zip");
        let archive = dir.join("deps.zip");
        let mut zip = zip::ZipWriter::new(File::create(&archive).unwrap());
        zip.start_file(
            "zp-1.0.dist-info/METADATA",
            zip::write::SimpleFileOptions::default(),
        )
        .unwrap();
        zip.write_all(b"Name: zp\nVersion: 1.0\n").unwrap();
        zip.finish().unwrap();
        let result = find(&archive, "ZP");
        fs::remove_dir_all(&dir).unwrap();
        assert_eq!(result, found("zp", "1.0"));
    }

    #[test]
    fn normalized_names() {
        assert_eq!(normalize("zope.interface"), "zope_interface");
        assert_eq!(normalize("a-_.b"), "a_b");
    }
}
