use datadog_library_config::{Configurator, ProcessInfo};
use pyo3::exceptions::PyException;
use pyo3::prelude::*;
use pyo3::types::PyDict;

#[pyclass(name = "PyConfigurator", module = "ddtrace.internal._native")]
pub struct PyConfigurator {
    configurator: Box<Configurator>,
    local_file: String,
    fleet_file: String,
}

#[pymethods]
impl PyConfigurator {
    #[new]
    pub fn new(debug_logs: bool) -> Self {
        PyConfigurator {
            configurator: Box::new(Configurator::new(debug_logs)),
            fleet_file: Configurator::FLEET_STABLE_CONFIGURATION_PATH.to_string(),
            local_file: Configurator::LOCAL_STABLE_CONFIGURATION_PATH.to_string(),
        }
    }

    pub fn set_local_file_override(&mut self, file: String) -> PyResult<()> {
        self.local_file = file;
        Ok(())
    }

    pub fn get_configuration(&self, py: Python<'_>) -> PyResult<PyObject> {
        let res_config = self.configurator.get_config_from_file(
            self.local_file.as_ref(),
            self.fleet_file.as_ref(),
            ProcessInfo::detect_global("python".to_string()),
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
