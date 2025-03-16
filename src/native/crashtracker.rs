use std::collections::HashMap;
use std::sync::atomic::{AtomicU8, Ordering};
use std::sync::Once;

use datadog_crashtracker::{
    CrashtrackerConfiguration, CrashtrackerReceiverConfig, Metadata, StacktraceCollection,
};
use ddcommon::Endpoint;
use pyo3::prelude::*;

#[macro_export]
macro_rules! to_pyresult {
    ($expr:expr) => {
        $expr.map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))
    };
}

pub trait RustWrapper {
    type Inner;
    const INNER_TYPE_NAME: &'static str;
    fn take_inner(&mut self) -> Option<Self::Inner>;
    fn take_inner_or_err(&mut self) -> PyResult<Self::Inner> {
        self.take_inner().ok_or_else(|| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!(
                "{} is None",
                Self::INNER_TYPE_NAME
            ))
        })
    }
}

// We redefine the Enum here to expose it to Python as datadog_crashtracker::StacktraceCollection
// is defined in an external crate.
#[pyclass(
    eq,
    eq_int,
    name = "StacktraceCollection",
    module = "datadog.internal._native"
)]
#[derive(Clone, PartialEq)]
pub enum StacktraceCollectionPy {
    Disabled,
    WithoutSymbols,
    EnabledWithInprocessSymbols,
    EnabledWithSymbolsInReceiver,
}

impl From<StacktraceCollectionPy> for StacktraceCollection {
    fn from(value: StacktraceCollectionPy) -> Self {
        match value {
            StacktraceCollectionPy::Disabled => StacktraceCollection::Disabled,
            StacktraceCollectionPy::WithoutSymbols => StacktraceCollection::WithoutSymbols,
            StacktraceCollectionPy::EnabledWithInprocessSymbols => {
                StacktraceCollection::EnabledWithInprocessSymbols
            }
            StacktraceCollectionPy::EnabledWithSymbolsInReceiver => {
                StacktraceCollection::EnabledWithSymbolsInReceiver
            }
        }
    }
}

#[pyclass(
    name = "CrashtrackerConfiguration",
    module = "datadog.internal._native"
)]
#[derive(Clone)]
pub struct CrashtrackerConfigurationPy {
    config: Option<CrashtrackerConfiguration>,
}

#[pymethods]
impl CrashtrackerConfigurationPy {
    #[new]
    #[pyo3(signature = (additional_files, create_alt_stack, use_alt_stack, timeout_ms, resolve_frames, endpoint=None, unix_socket_path=None))]
    pub fn new(
        additional_files: Vec<String>,
        create_alt_stack: bool,
        use_alt_stack: bool,
        timeout_ms: u32,
        resolve_frames: StacktraceCollectionPy,
        endpoint: Option<&str>,
        unix_socket_path: Option<String>,
    ) -> PyResult<Self> {
        let resolve_frames: StacktraceCollection = resolve_frames.into();
        let endpoint = endpoint.map(Endpoint::from_slice);

        Ok(Self {
            config: Some(to_pyresult!(CrashtrackerConfiguration::new(
                additional_files,
                create_alt_stack,
                use_alt_stack,
                endpoint,
                resolve_frames,
                timeout_ms,
                unix_socket_path,
            ))),
        })
    }
}

impl RustWrapper for CrashtrackerConfigurationPy {
    type Inner = CrashtrackerConfiguration;
    const INNER_TYPE_NAME: &'static str = "CrashtrackerConfiguration";

    fn take_inner(&mut self) -> Option<Self::Inner> {
        self.config.take()
    }
}

#[pyclass(
    name = "CrashtrackerReceiverConfig",
    module = "datadog.internal._native"
)]
#[derive(Clone)]
pub struct CrashtrackerReceiverConfigPy {
    config: Option<CrashtrackerReceiverConfig>,
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
    ) -> PyResult<Self> {
        Ok(Self {
            config: Some(to_pyresult!(CrashtrackerReceiverConfig::new(
                args,
                env.into_iter().collect(),
                path_to_receiver_binary,
                stderr_filename,
                stdout_filename,
            ))),
        })
    }
}

impl RustWrapper for CrashtrackerReceiverConfigPy {
    type Inner = CrashtrackerReceiverConfig;
    const INNER_TYPE_NAME: &'static str = "CrashtrackerReceiverConfig";

    fn take_inner(&mut self) -> Option<Self::Inner> {
        self.config.take()
    }
}

#[pyclass(name = "Metadata", module = "datadog.internal._native")]
#[derive(Clone)]
pub struct MetadataPy {
    metadata: Option<Metadata>,
}

#[pymethods]
impl MetadataPy {
    #[new]
    pub fn new(
        library_name: String,
        library_version: String,
        family: String,
        tags: HashMap<String, String>,
    ) -> PyResult<Self> {
        Ok(Self {
            metadata: Some(Metadata::new(
                library_name,
                library_version,
                family,
                tags.into_iter().map(|(k, v)| format!("{k}:{v}")).collect(),
            )),
        })
    }
}

impl RustWrapper for MetadataPy {
    type Inner = Metadata;
    const INNER_TYPE_NAME: &'static str = "Metadata";

    fn take_inner(&mut self) -> Option<Self::Inner> {
        self.metadata.take()
    }
}

#[repr(u8)]
#[pyclass(
    eq,
    eq_int,
    name = "CrashtrackerStatus",
    module = "datadog.internal._native"
)]
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
pub fn crashtracker_init<'py>(
    config: &Bound<'py, CrashtrackerConfigurationPy>,
    receiver_config: &Bound<'py, CrashtrackerReceiverConfigPy>,
    metadata: &Bound<'py, MetadataPy>,
) -> PyResult<()> {
    INIT.call_once(|| {
        let (config_opt, receiver_config_opt, metadata_opt) = (
            config.try_borrow_mut().ok().and_then(|mut c| (*c).take_inner()),
            receiver_config.try_borrow_mut().ok().and_then(|mut rc| (*rc).take_inner()),
            metadata.try_borrow_mut().ok().and_then(|mut m| (*m).take_inner()),
        );

        if let (Some(config), Some(receiver_config), Some(metadata)) =
            (config_opt, receiver_config_opt, metadata_opt)
        {
            match datadog_crashtracker::init(config, receiver_config, metadata) {
                Ok(_) => CRASHTRACKER_STATUS
                    .store(CrashtrackerStatus::Initialized as u8, Ordering::SeqCst),
                Err(e) => {
                    eprintln!("Failed to initialize crashtracker: {}", e);
                    CRASHTRACKER_STATUS.store(
                        CrashtrackerStatus::FailedToInitialize as u8,
                        Ordering::SeqCst,
                    );
                }
            }
        } else {
            eprintln!("Failed to initialize crashtracker: config, receiver_config, metadata inner values are None");
            CRASHTRACKER_STATUS.store(
                CrashtrackerStatus::FailedToInitialize as u8,
                Ordering::SeqCst,
            );
        }
    });
    Ok(())
}

#[pyfunction(name = "crashtracker_on_fork")]
pub fn crashtracker_on_fork<'py>(
    config: &Bound<'py, CrashtrackerConfigurationPy>,
    receiver_config: &Bound<'py, CrashtrackerReceiverConfigPy>,
    metadata: &Bound<'py, MetadataPy>,
) -> PyResult<()> {
    let inner_config: CrashtrackerConfiguration =
        (*config.try_borrow_mut()?).take_inner_or_err()?;
    let inner_receiver_config: CrashtrackerReceiverConfig =
        (*receiver_config.try_borrow_mut()?).take_inner_or_err()?;
    let inner_metadata: Metadata = (*metadata.try_borrow_mut()?).take_inner_or_err()?;
    // Note to self: is it possible to call crashtracker_on_fork before crashtracker_init?
    // dd-trace-py seems to start crashtracker early on.
    to_pyresult!(datadog_crashtracker::on_fork(
        inner_config,
        inner_receiver_config,
        inner_metadata,
    ))
}

#[pyfunction(name = "crashtracker_status")]
pub fn crashtracker_status() -> PyResult<CrashtrackerStatus> {
    CrashtrackerStatus::try_from(CRASHTRACKER_STATUS.load(Ordering::SeqCst))
}

// We expose the receiver_entry_point_stdin to use from Python script, crashtracker_exe command.
// This is to avoid distributing both the binary and executable, which might increase the size of
// the package. This way results in referring to the same .so file from the crashtracker_exe script
// and Python library. Another side effect is that we no longer has to worry about platform specific
// binary names for crashtracker_exe, since it's just a Python script.
#[pyfunction(name = "crashtracker_receiver")]
pub fn crashtracker_receiver() -> PyResult<()> {
    to_pyresult!(datadog_crashtracker::receiver_entry_point_stdin())
}
