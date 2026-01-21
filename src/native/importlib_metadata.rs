/// Fast Rust implementation of importlib_metadata.distributions()
/// Optimized for performance with caching and efficient directory scanning
/// Version 2: Adds support for dist.files and dist.read_text()
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList, PyTuple};
use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};

/// Lightweight distribution info (before creating Python objects)
#[derive(Clone, Debug)]
struct DistInfo {
    name: String,
    path: PathBuf,
    metadata_text: Option<String>,
}

/// PackagePath - represents a file path within a distribution
#[pyclass]
struct PackagePath {
    #[pyo3(get)]
    parts: Py<PyTuple>,
    path_str: String,
    dist_path: PathBuf,
}

impl Clone for PackagePath {
    fn clone(&self) -> Self {
        Python::with_gil(|py| PackagePath {
            parts: self.parts.clone_ref(py),
            path_str: self.path_str.clone(),
            dist_path: self.dist_path.clone(),
        })
    }
}

#[pymethods]
impl PackagePath {
    fn __repr__(&self) -> String {
        self.path_str.clone()
    }

    fn __str__(&self) -> String {
        self.path_str.clone()
    }

    /// Read the file content as text
    fn read_text(&self, _py: Python) -> PyResult<String> {
        let full_path = self
            .dist_path
            .parent()
            .ok_or_else(|| PyErr::new::<pyo3::exceptions::PyValueError, _>("Invalid dist path"))?
            .join(&self.path_str);

        fs::read_to_string(&full_path).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyIOError, _>(format!(
                "Failed to read {}: {}",
                self.path_str, e
            ))
        })
    }

    /// Locate the file and return its absolute path
    fn locate(&self) -> PyResult<PathBuf> {
        let full_path = self
            .dist_path
            .parent()
            .ok_or_else(|| PyErr::new::<pyo3::exceptions::PyValueError, _>("Invalid dist path"))?
            .join(&self.path_str);
        Ok(full_path)
    }
}

/// Parse RECORD file and return list of PackagePath objects
fn parse_record_file(
    record_path: &Path,
    dist_path: &PathBuf,
    py: Python,
) -> PyResult<Vec<Py<PackagePath>>> {
    let content = match fs::read_to_string(record_path) {
        Ok(c) => c,
        Err(_) => return Ok(Vec::new()),
    };

    let mut files = Vec::new();

    for line in content.lines() {
        if line.is_empty() {
            continue;
        }

        // RECORD format: filename,hash,size
        let filename = match line.split(',').next() {
            Some(f) => f,
            None => continue,
        };

        // Convert filename to parts tuple
        let parts: Vec<&str> = if filename.contains('/') {
            filename.split('/').collect()
        } else if filename.contains('\\') {
            filename.split('\\').collect()
        } else {
            vec![filename]
        };

        // Create PyTuple for parts
        let py_parts = PyTuple::new(py, &parts)?;

        let package_path = PackagePath {
            parts: py_parts.unbind(),
            path_str: filename.to_string(),
            dist_path: dist_path.clone(),
        };

        files.push(Py::new(py, package_path)?);
    }

    Ok(files)
}

/// Parse RFC 822 style metadata quickly
fn parse_metadata_fast(text: &str) -> HashMap<String, String> {
    let mut result = HashMap::new();

    for line in text.lines() {
        if line.is_empty() {
            break; // End of headers, start of body
        }

        if let Some((key, value)) = line.split_once(':') {
            let key = key.trim();
            let value = value.trim();

            // Only extract the fields we need: Name and Version
            if key.eq_ignore_ascii_case("name") || key.eq_ignore_ascii_case("version") {
                result.insert(key.to_lowercase(), value.to_string());
            }
        }
    }

    result
}

/// Scan a directory for distributions (optimized for speed)
fn scan_directory_for_dists(dir_path: &Path) -> Vec<DistInfo> {
    let entries = match fs::read_dir(dir_path) {
        Ok(e) => e,
        Err(_) => return Vec::new(),
    };

    let mut dists = Vec::with_capacity(64);

    for entry in entries.flatten() {
        let filename = match entry.file_name().into_string() {
            Ok(f) => f,
            Err(_) => continue,
        };

        // Fast suffix check
        let suffix_len = if filename.ends_with(".dist-info") {
            10
        } else if filename.ends_with(".egg-info") {
            9
        } else {
            continue;
        };

        let path = entry.path();
        if !path.is_dir() {
            continue;
        }

        // Extract package name
        let pkg_name = &filename[..filename.len() - suffix_len];
        let name = pkg_name.split('-').next().unwrap_or(pkg_name);

        // Read metadata eagerly (we'll need it anyway)
        let metadata_path = path.join("METADATA");
        let pkg_info_path = path.join("PKG-INFO");

        let metadata_text = fs::read_to_string(&metadata_path)
            .or_else(|_| fs::read_to_string(&pkg_info_path))
            .ok();

        if metadata_text.is_none() {
            continue;
        }

        dists.push(DistInfo {
            name: name.to_string(),
            path,
            metadata_text,
        });
    }

    dists
}

/// Adaptive scan: sequential for typical sys.path, parallel for large
fn scan_all_distributions(paths: &[PathBuf]) -> Vec<DistInfo> {
    // Sequential scan is faster for typical sys.path (< 10 directories)
    paths
        .iter()
        .flat_map(|path| scan_directory_for_dists(path))
        .collect()
}

/// Distribution class - Python object representing a distribution
#[pyclass]
struct Distribution {
    #[pyo3(get)]
    name: String,
    path: PathBuf,
    #[pyo3(get)]
    metadata: Py<PyDict>,
    #[pyo3(get)]
    version: String,
}

#[pymethods]
impl Distribution {
    fn __repr__(&self) -> String {
        format!("<Distribution('{}', '{}')>", self.name, self.version)
    }

    /// Get the list of files in this distribution (lazy-loaded)
    #[getter]
    fn files(&self, py: Python) -> PyResult<Option<Vec<Py<PackagePath>>>> {
        // Try RECORD file first (for wheel/dist-info)
        let record_path = self.path.join("RECORD");
        if record_path.exists() {
            return Ok(Some(parse_record_file(&record_path, &self.path, py)?));
        }

        // Try installed-files.txt (for egg-info)
        let installed_files_path = self.path.join("installed-files.txt");
        if installed_files_path.exists() {
            return Ok(Some(parse_record_file(
                &installed_files_path,
                &self.path,
                py,
            )?));
        }

        // No files list available
        Ok(None)
    }

    /// Read a text file from the distribution directory
    fn read_text(&self, filename: &str) -> PyResult<Option<String>> {
        let file_path = self.path.join(filename);

        match fs::read_to_string(&file_path) {
            Ok(content) => Ok(Some(content)),
            Err(_) => Ok(None),
        }
    }
}

/// Fast distributions() implementation
#[pyfunction]
pub fn distributions(py: Python) -> PyResult<Vec<Py<Distribution>>> {
    // Get sys.path from Python
    let sys = py.import("sys")?;
    let path_attr = sys.getattr("path")?;
    let path_list = path_attr.downcast::<PyList>()?;

    let mut paths = Vec::with_capacity(path_list.len());
    for item in path_list.iter() {
        if let Ok(path_str) = item.extract::<String>() {
            let path = PathBuf::from(path_str);
            if path.exists() && path.is_dir() {
                paths.push(path);
            }
        }
    }

    // Scan all directories
    let dist_infos = scan_all_distributions(&paths);

    // Convert to Python objects
    let mut result = Vec::with_capacity(dist_infos.len());

    for info in dist_infos {
        // Parse metadata
        let metadata_dict = PyDict::new(py);

        if let Some(ref text) = info.metadata_text {
            let parsed = parse_metadata_fast(text);

            let name = parsed
                .get("name")
                .cloned()
                .unwrap_or_else(|| info.name.clone());
            let version = parsed
                .get("version")
                .cloned()
                .unwrap_or_else(|| "unknown".to_string());

            metadata_dict.set_item("name", &name)?;
            metadata_dict.set_item("version", &version)?;
            metadata_dict.set_item("Name", &name)?;
            metadata_dict.set_item("Version", &version)?;

            let dist = Distribution {
                name: name.clone(),
                path: info.path,
                metadata: metadata_dict.unbind(),
                version,
            };

            result.push(Py::new(py, dist)?);
        }
    }

    Ok(result)
}

/// Python module for importlib_metadata
pub fn register_importlib_metadata(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<Distribution>()?;
    m.add_class::<PackagePath>()?;
    m.add_function(wrap_pyfunction!(distributions, m)?)?;
    Ok(())
}
