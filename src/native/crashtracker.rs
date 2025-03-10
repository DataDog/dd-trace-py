use std::collections::HashMap;
use std::sync::atomic::{AtomicU8, Ordering};
use std::sync::Once;

use datadog_crashtracker::{
    CrashtrackerConfiguration, CrashtrackerReceiverConfig, Metadata, StacktraceCollection,
};
use ddcommon::Endpoint;
use pyo3::prelude::*;

#[pyclass(
    name = "CrashtrackerConfiguration",
    module = "datadog.internal._crashtracker"
)]
#[derive(Clone)]
pub struct CrashtrackerConfigurationPy {
    config: CrashtrackerConfiguration,
}

#[pymethods]
impl CrashtrackerConfigurationPy {
    #[new]
    #[pyo3(signature = (additional_files, create_alt_stack, use_alt_stack, timeout_ms, endpoint=None, resolve_frames=None, unix_socket_path=None))]
    pub fn new(
        additional_files: Vec<String>,
        create_alt_stack: bool,
        use_alt_stack: bool,
        timeout_ms: u32,
        endpoint: Option<&str>,
        resolve_frames: Option<&str>,
        unix_socket_path: Option<String>,
    ) -> Result<Self, PyErr> {
        let resolve_frames: StacktraceCollection = match resolve_frames {
            Some(s) => match s {
                "full" => StacktraceCollection::EnabledWithInprocessSymbols,
                "fast" => StacktraceCollection::WithoutSymbols,
                "safe" => StacktraceCollection::EnabledWithSymbolsInReceiver,
                _ => StacktraceCollection::EnabledWithInprocessSymbols,
            },
            None => StacktraceCollection::EnabledWithInprocessSymbols,
        };

        let endpoint = endpoint.map(Endpoint::from_slice);

        Ok(Self {
            config: CrashtrackerConfiguration::new(
                additional_files,
                create_alt_stack,
                use_alt_stack,
                endpoint,
                resolve_frames,
                timeout_ms,
                unix_socket_path,
            )
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))?,
        })
    }
}

#[pyclass(
    name = "CrashtrackerReceiverConfig",
    module = "datadog.internal._crashtracker"
)]
#[derive(Clone)]
pub struct CrashtrackerReceiverConfigPy {
    config: CrashtrackerReceiverConfig,
}

#[pymethods]
impl CrashtrackerReceiverConfigPy {
    #[new]
    #[pyo3(signature = (args, env, path_to_receiver_binary, stderr_filename=None, stdout_filename=None))]
    pub fn new(
        args: Vec<String>,
        env: HashMap<String, String>,
        path_to_receiver_binary: String,
        stderr_filename: Option<String>,
        stdout_filename: Option<String>,
    ) -> Result<Self, PyErr> {
        Ok(Self {
            config: CrashtrackerReceiverConfig::new(
                args,
                env.into_iter().collect(),
                path_to_receiver_binary,
                stderr_filename,
                stdout_filename,
            )
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))?,
        })
    }
}

#[pyclass(name = "Metadata", module = "datadog.internal._crashtracker")]
#[derive(Clone)]
pub struct MetadataPy {
    metadata: Metadata,
}

#[pymethods]
impl MetadataPy {
    #[new]
    pub fn new(
        library_name: String,
        library_version: String,
        family: String,
        tags: HashMap<String, String>,
    ) -> Self {
        Self {
            metadata: Metadata::new(
                library_name,
                library_version,
                family,
                tags.into_iter()
                    .map(|(k, v)| format!("{}:{}", k, v))
                    .collect(),
            ),
        }
    }
}

#[repr(u8)]
#[pyclass(eq, eq_int)]
#[derive(PartialEq)]
pub enum CrashtrackerStatus {
    NotInitialized = 0,
    Initialized = 1,
    FailedToInitialize = 2,
}

impl std::convert::TryFrom<u8> for CrashtrackerStatus {
    type Error = PyErr;

    fn try_from(value: u8) -> Result<Self, Self::Error> {
        match value {
            0 => Ok(CrashtrackerStatus::NotInitialized),
            1 => Ok(CrashtrackerStatus::Initialized),
            2 => Ok(CrashtrackerStatus::FailedToInitialize),
            _ => Err(pyo3::exceptions::PyValueError::new_err(
                "Invalid value for CrashtrackerStatus",
            )),
        }
    }
}

static CRASHTRACKER_STATUS: AtomicU8 = AtomicU8::new(CrashtrackerStatus::NotInitialized as u8);
static INIT: Once = Once::new();

#[pyfunction(name = "crashtracker_init")]
pub fn crashtracker_init(
    config: CrashtrackerConfigurationPy,
    receiver_config: CrashtrackerReceiverConfigPy,
    metadata: MetadataPy,
) -> Result<(), PyErr> {
    INIT.call_once(|| {
        let result =
            datadog_crashtracker::init(config.config, receiver_config.config, metadata.metadata);
        match result {
            Ok(_) => {
                CRASHTRACKER_STATUS.store(CrashtrackerStatus::Initialized as u8, Ordering::SeqCst)
            }
            Err(e) => {
                eprintln!("Failed to initialize crashtracker: {}", e);
                CRASHTRACKER_STATUS.store(
                    CrashtrackerStatus::FailedToInitialize as u8,
                    Ordering::SeqCst,
                );
            }
        }
    });
    Ok(())
}

#[pyfunction(name = "crashtracker_status")]
pub fn crashtracker_status() -> Result<CrashtrackerStatus, PyErr> {
    CrashtrackerStatus::try_from(CRASHTRACKER_STATUS.load(Ordering::SeqCst))
}

#[pyfunction(name = "crashtracker_on_fork")]
pub fn crashtracker_on_fork(
    config: CrashtrackerConfigurationPy,
    receiver_config: CrashtrackerReceiverConfigPy,
    metadata: MetadataPy,
) -> Result<(), PyErr> {
    // Note to self: is it possible to call crashtracker_on_fork before crashtracker_init?
    // dd-trace-py seems to start crashtracker early on.
    datadog_crashtracker::on_fork(config.config, receiver_config.config, metadata.metadata)
        .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))
}

// We expose the receiver_entry_point_stdin to use from Python script, crashtracker_exe command.
// This is to avoid distributing both the binary and executable, which might increase the size of
// the package. This way results in referring to the same .so file from the crashtracker_exe script
// and Python library. Another side effect is that we no longer has to worry about platform specific
// binary names for crashtracker_exe, since it's just a Python script.
#[cfg(unix)]
#[pyfunction(name = "crashtracker_receiver")]
pub fn crashtracker_receiver() -> Result<(), PyErr> {
    datadog_crashtracker::receiver_entry_point_stdin()
        .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))
}

#[cfg(not(unix))]
#[pyfunction(name = "crashtracker_receiver")]
pub fn crashtracker_receiver() -> Result<(), PyErro> {
    Ok(())
}
