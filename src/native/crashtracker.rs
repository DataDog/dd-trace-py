use std::collections::HashMap;

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

#[pyfunction(
    name = "crashtracker_init",
)]
pub fn crashtracker_init(
    config: CrashtrackerConfigurationPy,
    receiver_config: CrashtrackerReceiverConfigPy,
    metadata: MetadataPy,
) -> Result<(), PyErr> {
    datadog_crashtracker::init(config.config, receiver_config.config, metadata.metadata)
        .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))
}

#[pyfunction(
    name = "crashtracker_on_fork",
)]
pub fn crashtracker_on_fork(
    config: CrashtrackerConfigurationPy,
    receiver_config: CrashtrackerReceiverConfigPy,
    metadata: MetadataPy,
) -> Result<(), PyErr> {
    datadog_crashtracker::on_fork(config.config, receiver_config.config, metadata.metadata)
        .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))
}
