use pyo3::pymodule;

#[pymodule]
pub mod logger {
    use pyo3::prelude::*;
    use pyo3::types::PyDict;
    use pyo3::{exceptions::PyValueError, PyResult};

    use datadog_log::logger::{
        logger_configure_file, logger_configure_std, logger_disable_file, logger_disable_std,
        logger_set_log_level, FileConfig, LogEventLevel, StdConfig, StdTarget,
    };
    use tracing::{debug, error, info, trace, warn};

    #[pyfunction]
    #[pyo3(signature = (**kwds))]
    fn configure(kwds: Option<&Bound<'_, PyDict>>) -> PyResult<()> {
        let output;

        if let Some(kwargs) = kwds {
            output = kwargs
                .get_item("output")?
                .ok_or_else(|| PyValueError::new_err("Missing output argument"))?
                .extract()?;
        } else {
            output = "stdout".to_string();
        }

        match output.as_str() {
            "stdout" => logger_configure_std(StdConfig {
                target: StdTarget::Out,
            })
            .map_err(|e| PyValueError::new_err(e.to_string())),
            "stderr" => logger_configure_std(StdConfig {
                target: StdTarget::Err,
            })
            .map_err(|e| PyValueError::new_err(e.to_string())),
            "file" => {
                let kwargs =
                    kwds.ok_or_else(|| PyValueError::new_err("Missing arguments for file"))?;

                let path: String = kwargs
                    .get_item("path")?
                    .ok_or_else(|| {
                        PyValueError::new_err("Missing required argument for file: path")
                    })?
                    .extract()?;

                let max_files: u64 = kwargs
                    .get_item("max_files")?
                    .map(|v| v.extract())
                    .transpose()?
                    .unwrap_or(0);

                let max_size_bytes: u64 = kwargs
                    .get_item("max_size_bytes")?
                    .map(|v| v.extract())
                    .transpose()?
                    .unwrap_or(0);

                let cfg = FileConfig {
                    path,
                    max_files,
                    max_size_bytes,
                };
                logger_configure_file(cfg).map_err(|e| PyValueError::new_err(e.to_string()))
            }
            other => Err(PyValueError::new_err(format!("Invalid output: {other}"))),
        }
    }

    /// Disable logging output by type: "file", "stdout", "stderr"
    #[pyfunction]
    fn disable(output: &str) -> PyResult<()> {
        match output {
            "file" => logger_disable_file().map_err(|e| PyValueError::new_err(e.to_string())),
            "stdout" | "stderr" => {
                logger_disable_std().map_err(|e| PyValueError::new_err(e.to_string()))
            }
            other => Err(PyValueError::new_err(format!("Invalid output: {other}"))),
        }
    }

    /// Set log level (trace, debug, info, warn, error)
    #[pyfunction]
    fn set_log_level(level: &str) -> PyResult<()> {
        let rust_level = match level.to_lowercase().as_str() {
            "trace" => LogEventLevel::Trace,
            "debug" => LogEventLevel::Debug,
            "info" => LogEventLevel::Info,
            "warn" => LogEventLevel::Warn,
            "error" => LogEventLevel::Error,
            other => return Err(PyValueError::new_err(format!("Invalid log level: {other}"))),
        };
        logger_set_log_level(rust_level).map_err(|e| PyValueError::new_err(e.to_string()))
    }

    /// Logs a message
    #[pyfunction]
    fn log(level: &str, message: &str) -> PyResult<()> {
        match level.to_lowercase().as_str() {
            "trace" => trace!("{}", message),
            "debug" => debug!("{}", message),
            "info" => info!("{}", message),
            "warn" => warn!("{}", message),
            "error" => error!("{}", message),
            other => return Err(PyValueError::new_err(format!("Invalid log level: {other}"))),
        }
        Ok(())
    }
}
