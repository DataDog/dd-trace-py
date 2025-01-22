use datadog_library_config::{Configurator, ProcessInfo};
use pyo3::exceptions::PyException;
use pyo3::prelude::*;
use pyo3::types::PyDict;

#[pyclass(name = "PyConfigurator", module = "ddtrace.internal._native")]
pub struct PyConfigurator {
    configurator: Box<Configurator>,
    envp: Vec<String>,
    args: Vec<String>,
}

#[pymethods]
impl PyConfigurator {
    #[new]
    pub fn new(debug_logs: bool) -> Self {
        PyConfigurator {
            configurator: Box::new(Configurator::new(debug_logs)),
            envp: Vec::new(),
            args: Vec::new(),
        }
    }

    pub fn set_envp(&mut self, envp: Vec<String>) -> PyResult<()> {
        self.envp = envp;
        Ok(())
    }

    pub fn set_args(&mut self, args: Vec<String>) -> PyResult<()> {
        self.args = args;
        Ok(())
    }

    pub fn get_configuration(&self, py: Python<'_>) -> PyResult<PyObject> {
        let envp: Vec<&[u8]> = self.envp.iter().map(|s| s.as_bytes()).collect();
        let args: Vec<&[u8]> = self.args.iter().map(|s| s.as_bytes()).collect();

        let process_info = ProcessInfo {
            envp: &envp,
            args: &args,
            language: b"python",
        };

        let res_config = self.configurator.get_config_from_file(
            "/etc/datadog-agent/managed/datadog-apm-libraries/stable/libraries_config.yaml"
                .as_ref(),
            process_info,
        );
        match res_config {
            Ok(config) => {
                let dict = PyDict::new_bound(py);
                for c in config.iter() {
                    let key = c.name.to_str().to_owned();
                    let _ = dict.set_item(key, c.value.clone());
                }
                Ok(dict.into())
            }
            Err(e) => {
                let err_msg = format!("Failed to get configuration: {:?}", e);
                Err(PyException::new_err(err_msg))
            }
        }
    }
}
